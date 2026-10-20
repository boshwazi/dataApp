"""Row-level security in Postgres on its own: with no app code in between, a user sees and changes
only what their role in their own workspace allows. Runs against DATABASE_URL in .env, in a
throwaway schema; skipped without it."""

import json
import uuid

import psycopg
import pytest

from app.db import Database
from app.workspaces import User
from test_workspaces import DATABASE_URL

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="No DATABASE_URL in .env")


def person(name: str) -> User:
    return User(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"datawazi-rls/{name}")), email=f"{name}@example.com")


ANA, BEN, VIC = person("ana"), person("ben"), person("vic")


@pytest.fixture(scope="module")
def db():
    schema = f"dwrls_{uuid.uuid4().hex[:10]}"
    database = Database(DATABASE_URL, schema=schema, pool_size=2)
    database.migrate()
    t = database.t
    with database.tx(None) as con:  # as the service: two companies, Vic a viewer at Ana's
        for ws, owner in (("ws_ana", ANA), ("ws_ben", BEN)):
            con.execute(f"insert into {t('workspaces')} (id, name) values (%s, %s)", [ws, ws])
            con.execute(f"insert into {t('members')} (workspace_id, user_id, email, role) values (%s, %s, %s, 'admin')",
                        [ws, owner.id, owner.email])
            con.execute(f"insert into {t('records')} (kind, id, workspace_id, record) values ('dataset', %s, %s, %s)",
                        [f"d_{ws}", ws, json.dumps({"id": f"d_{ws}", "name": f"{ws} sales"})])
        con.execute(f"insert into {t('members')} (workspace_id, user_id, email, role) values ('ws_ana', %s, %s, 'viewer')",
                    [VIC.id, VIC.email])
    yield database
    database.close()
    with psycopg.connect(DATABASE_URL, autocommit=True) as con:
        con.execute(f"drop schema if exists {schema} cascade")


def visible(db, user, table, column="workspace_id"):
    with db.tx(user) as con:
        return sorted({r[column] for r in con.execute(f"select {column} from {db.t(table)}")})


def test_each_user_sees_only_their_own_workspace(db):
    assert visible(db, ANA, "records") == ["ws_ana"]
    assert visible(db, BEN, "records") == ["ws_ben"]
    assert visible(db, ANA, "members") == ["ws_ana"]
    assert visible(db, BEN, "workspaces", "id") == ["ws_ben"]
    assert visible(db, VIC, "records") == ["ws_ana"]  # viewers can read their workspace
    stranger = person("stranger")
    assert visible(db, stranger, "records") == [] and visible(db, stranger, "workspaces", "id") == []


def test_nobody_writes_into_another_workspace(db):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.tx(BEN) as con:
            con.execute(f"insert into {db.t('records')} (kind, id, workspace_id, record) values ('dataset', 'x1', 'ws_ana', '{{}}')")
    with db.tx(BEN) as con:  # updates and deletes simply find nothing to touch
        assert con.execute(f"update {db.t('records')} set record = '{{}}' where workspace_id = 'ws_ana'").rowcount == 0
        assert con.execute(f"delete from {db.t('records')} where workspace_id = 'ws_ana'").rowcount == 0
        assert con.execute(f"update {db.t('members')} set role = 'viewer' where workspace_id = 'ws_ana'").rowcount == 0


def test_viewers_cannot_change_data_or_people(db):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.tx(VIC) as con:
            con.execute(f"insert into {db.t('records')} (kind, id, workspace_id, record) values ('dashboard', 'x2', 'ws_ana', '{{}}')")
    with db.tx(VIC) as con:
        assert con.execute(f"update {db.t('records')} set record = '{{}}' where workspace_id = 'ws_ana'").rowcount == 0
        assert con.execute(f"update {db.t('members')} set role = 'admin' where user_id = %s", [VIC.id]).rowcount == 0
        assert con.execute(f"select count(*) from {db.t('invites')}").fetchone()["count"] == 0  # invites are admin-only


def test_tokens_are_never_stored_and_invites_check_the_email(db):
    from app.db import PgWorkspaceStore
    store = PgWorkspaceStore(db)
    from app import workspaces
    with workspaces.use("ws_ben", user=BEN):
        sent = store.invite("ws_ben", "dan@example.com", "creator", BEN)
    with db.tx(None) as con:
        stored = con.execute(f"select * from {db.t('invites')} where email = 'dan@example.com'").fetchone()
    assert sent["token"] not in json.dumps(stored, default=str)  # only its hash is kept
    with pytest.raises(workspaces.WorkspaceError, match="for dan@example.com"):
        store.accept(sent["token"], ANA)
    dan = person("dan")
    dan = User(id=dan.id, email="dan@example.com")
    assert store.accept(sent["token"], dan)["id"] == "ws_ben"
    assert visible(db, dan, "records") == ["ws_ben"]
