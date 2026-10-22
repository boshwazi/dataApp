"""Datasets ("projects" in the code) and their data sources, stored on local disk.

A dataset is the Get Data → Transform → Model half of the app, reused by any number of
dashboards. One folder per dataset:
  <root>/<id>/project.json              name, sources, transform steps, model, published snapshot
  <root>/<id>/data.duckdb               the draft: raw tables, the clean schema, the model schema
  <root>/<id>/published-v<n>.duckdb     a frozen copy that dashboards read

Sources are loaded exactly as read (every column VARCHAR, blanks as NULL). Choosing
types and cleaning values are Transform steps, so they stay visible and undoable.

Everything goes through these stores so the hosted phase can swap in Postgres and
object storage. Every record carries a workspace_id from day one.
"""

import json
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from . import profiling

LOCAL_WORKSPACE = "local"
ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")
MAX_NAME_LENGTH = 60


class NotFound(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def table_name(text: str) -> str:
    """A safe, lower-case SQL identifier from a file, sheet or user-given name."""
    name = re.sub(r"[^0-9a-zA-Z]+", "_", text.strip()).strip("_").lower()
    if not name:
        name = "table"
    if name[0].isdigit():
        name = f"t_{name}"
    return name[:MAX_NAME_LENGTH]


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


SQL_TYPE = re.compile(r"^[A-Z][A-Z0-9_]*(\(\d+(, ?\d+)?\))?$")


def load_sql(types: dict[str, str] | None) -> str:
    """SELECT from the registered frame, casting back to the original column types when known.

    pandas turns DATE into TIMESTAMP on the way through, so linked tables carry their types."""
    if not types:
        return "SELECT * FROM incoming"
    for kind in types.values():
        if not SQL_TYPE.match(kind):
            raise ValueError(f"Unexpected column type {kind!r}.")
    return "SELECT " + ", ".join(f"CAST({quote(c)} AS {t}) AS {quote(c)}" for c, t in types.items()) + " FROM incoming"


def retrying(action, attempts: int = 20):
    """Windows refuses to replace a file another thread is reading (and to read it mid-replace): wait and retry."""
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


class FolderStore:
    """JSON records kept one per folder, with a lock so concurrent requests don't overwrite each other."""

    filename = "record.json"
    label = "Record"

    def __init__(self, root: Path | Callable[[], Path], workspace: Callable[[], str] = lambda: LOCAL_WORKSPACE,
                 records=None):
        # root may follow the current workspace (see workspaces.folder_for); a fixed path suits tests and tools.
        # records, when given (db.PgRecords), keeps the JSON in Postgres; data files stay in the folders.
        self._root = root
        self.workspace = workspace
        self.records = records
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root() if callable(self._root) else self._root

    def all(self) -> list[dict]:
        if self.records is not None:
            return self.records.all(self.workspace())
        if not self.root.exists():
            return []
        records = [json.loads(path.read_text(encoding="utf-8")) for path in self.root.glob(f"*/{self.filename}")]
        return sorted(records, key=lambda r: r["updated_at"], reverse=True)

    def get(self, record_id: str) -> dict:
        if self.records is not None:
            self._folder(record_id)  # checks the id's shape
            record = self.records.get(self.workspace(), record_id)
            if record is None:
                raise NotFound(f"{self.label} not found.")
            return record
        path = self._folder(record_id) / self.filename
        if not path.exists():
            raise NotFound(f"{self.label} not found.")
        return json.loads(retrying(lambda: path.read_text(encoding="utf-8")))

    def save(self, record: dict) -> None:
        record["updated_at"] = now()
        if self.records is not None:
            self._folder(record["id"]).mkdir(parents=True, exist_ok=True)  # its data files live here
            with self._lock:
                self.records.save(self.workspace(), record)
            return
        folder = self._folder(record["id"])
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{self.filename}.tmp"
        with self._lock:
            tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
            retrying(lambda: tmp.replace(folder / self.filename))

    @contextmanager
    def edit(self, record_id: str):
        """Load, change and save a record as one step."""
        with self._lock:
            record = self.get(record_id)
            yield record
            self.save(record)

    def rename(self, record_id: str, name: str) -> dict:
        with self.edit(record_id) as record:
            record["name"] = name.strip() or record["name"]
        return record

    def delete(self, record_id: str) -> None:
        folder = self._folder(record_id)
        if self.records is not None:
            with self._lock:
                if not self.records.delete(self.workspace(), record_id):
                    raise NotFound(f"{self.label} not found.")
                shutil.rmtree(folder, ignore_errors=True)
            return
        if not folder.exists():
            raise NotFound(f"{self.label} not found.")
        with self._lock:
            shutil.rmtree(folder)

    def _folder(self, record_id: str) -> Path:
        if not isinstance(record_id, str) or not ID_PATTERN.match(record_id):
            raise NotFound(f"{self.label} not found.")
        return self.root / record_id


class ProjectStore(FolderStore):
    filename = "project.json"
    label = "Dataset"
    backup = None  # supabase_admin.PublishedBackup when storage is configured
    on_publish = None  # called with a dataset id after it publishes (see main.py: insights, linked datasets)

    def create(self, name: str) -> dict:
        project = {
            "id": new_id(),
            "workspace_id": self.workspace(),
            "name": name.strip() or "Untitled dataset",
            "created_at": now(),
            "updated_at": now(),
            "sources": [],
            "published": None,
        }
        self.save(project)
        return project

    # Sources

    def add_source(self, project_id: str, frame: pd.DataFrame, name: str, origin: dict, warnings: list[str],
                   types: dict[str, str] | None = None) -> dict:
        with self.edit(project_id) as project, self.connect(project_id) as con:
            table = unique(table_name(name), {s["table"] for s in project["sources"]})
            con.register("incoming", frame)
            con.execute(f"CREATE TABLE {quote(table)} AS {load_sql(types)}")
            con.unregister("incoming")
            source = {
                "id": new_id(),
                "workspace_id": project["workspace_id"],
                "table": table,
                "origin": origin,
                "added_at": now(),
                "warnings": warnings,
                "profile": profiling.profile_table(con, table),
                "ai": None,
            }
            project["sources"].append(source)
        return source

    def source(self, project: dict, source_id: str) -> dict:
        for source in project["sources"]:
            if source["id"] == source_id:
                return source
        raise NotFound("Source not found.")

    def rename_source(self, project_id: str, source_id: str, name: str) -> dict:
        with self.edit(project_id) as project:
            source = self.source(project, source_id)
            new_table = table_name(name)
            if new_table in {s["table"] for s in project["sources"] if s["id"] != source_id}:
                raise ValueError(f"A table named {new_table} already exists.")
            if new_table != source["table"]:
                with self.connect(project_id) as con:
                    con.execute(f"ALTER TABLE {quote(source['table'])} RENAME TO {quote(new_table)}")
                source["table"] = new_table
        return source

    def delete_source(self, project_id: str, source_id: str) -> None:
        with self.edit(project_id) as project:
            source = self.source(project, source_id)
            with self.connect(project_id) as con:
                con.execute(f"DROP TABLE IF EXISTS {quote(source['table'])}")
            project["sources"].remove(source)

    def reprofile(self, project_id: str, source_id: str) -> dict:
        with self.edit(project_id) as project, self.connect(project_id) as con:
            source = self.source(project, source_id)
            source["profile"] = profiling.profile_table(con, source["table"])
            source["ai"] = None  # the old description may no longer match
        return source

    def set_ai(self, project_id: str, source_id: str, ai: dict) -> dict:
        with self.edit(project_id) as project:
            source = self.source(project, source_id)
            source["ai"] = ai
        return source

    def preview(self, project: dict, source_id: str, limit: int = 100, offset: int = 0) -> dict:
        source = self.source(project, source_id)
        with self.connect(project["id"]) as con:
            result = con.execute(f"SELECT * FROM {quote(source['table'])} LIMIT ? OFFSET ?", [limit, offset])
            columns = [d[0] for d in result.description]
            rows = result.fetchall()
        return {"columns": columns, "rows": rows, "total": source["profile"]["rows"], "offset": offset}

    def sample_rows(self, project: dict, source_id: str, limit: int = 5) -> list[dict]:
        preview = self.preview(project, source_id, limit)
        return [dict(zip(preview["columns"], row)) for row in preview["rows"]]

    @contextmanager
    def connect(self, project_id: str):
        # No reading or writing other files or URLs from SQL: all data arrives through ingest.
        con = duckdb.connect(str(self._folder(project_id) / "data.duckdb"), config={"enable_external_access": False})
        try:
            yield con
        finally:
            con.close()

    # Published copies

    def copy_for_publishing(self, project_id: str, version: int) -> str:
        """Freeze the draft data file as version `version`. Call while holding the dataset's edit lock."""
        folder = self._folder(project_id)
        with self.connect(project_id) as con:
            con.execute("CHECKPOINT")  # everything into the main file before copying it
        name = f"published-v{version}.duckdb"
        shutil.copyfile(folder / "data.duckdb", folder / name)
        if self.backup is not None:
            self.backup.save(self.workspace(), project_id, name, (folder / name).read_bytes())
        return name

    def copy_published(self, project_id: str, filename: str, version: int) -> str:
        """An earlier published copy, again as version `version` (rolling back)."""
        folder = self._folder(project_id)
        name = f"published-v{version}.duckdb"
        with self.connect_published(project_id, filename):
            pass  # fetches it from storage if this server doesn't have it
        shutil.copyfile(folder / filename, folder / name)
        if self.backup is not None:
            self.backup.save(self.workspace(), project_id, name, (folder / name).read_bytes())
        return name

    def published(self, project_id: str) -> None:
        """Tell whoever is listening that a dataset published a new version (after its edit is saved)."""
        if self.on_publish is not None:
            self.on_publish(project_id)

    def remove_old_copies(self, project_id: str, keep: str | set[str]) -> None:
        keep = {keep} if isinstance(keep, str) else keep
        old = []
        for path in self._folder(project_id).glob("published-v*.duckdb"):
            if path.name not in keep:
                old.append(path.name)
                try:
                    path.unlink()
                except OSError:
                    pass  # still open by a dashboard request; removed next time
        if self.backup is not None and old:
            self.backup.forget(self.workspace(), project_id, old)

    @contextmanager
    def connect_published(self, project_id: str, filename: str):
        if not re.fullmatch(r"published-v\d+\.duckdb", filename or ""):
            raise NotFound("This dataset has no published version.")
        path = self._folder(project_id) / filename
        if not path.exists() and self.backup is not None:
            # A new or rebuilt server: fetch the published copy from storage.
            data = self.backup.restore(self.workspace(), project_id, filename)
            if data:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(path)
        if not path.exists():
            raise NotFound("The published copy of this dataset is missing; publish it again.")
        con = duckdb.connect(str(path), read_only=True, config={"enable_external_access": False})
        try:
            yield con
        finally:
            con.close()


def unique(name: str, taken: set[str]) -> str:
    candidate, n = name, 2
    while candidate in taken:
        suffix = f"_{n}"
        candidate = name[: MAX_NAME_LENGTH - len(suffix)] + suffix
        n += 1
    return candidate
