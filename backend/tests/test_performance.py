"""M14: work that only happens when it has to, and profiles of large tables."""
import time

import duckdb
from test_report import Project, clean_env, ok  # noqa: F401  (clean_env is an autouse fixture)

from app import profiling


def outputs(p):
    queries = ok(p.api.get(f"{p.dataset}/transform"))["queries"]
    return {q["name"]: {**q, **ok(p.api.get(f"{p.dataset}/transform/queries/{q['id']}"))} for q in queries}


def test_only_tables_whose_steps_or_data_changed_are_built_again(tmp_path):
    p = Project(tmp_path)
    before = outputs(p)
    time.sleep(1.1)  # profiles are stamped to the second
    ok(p.api.post(f"{p.dataset}/transform/queries/{before['sales']['id']}/steps",
                  json={"op": "trim", "params": {"columns": ["customer"]}}))
    after = outputs(p)
    assert after["sales"]["profile"]["profiled_at"] != before["sales"]["profile"]["profiled_at"]
    assert after["customers"]["profile"]["profiled_at"] == before["customers"]["profile"]["profiled_at"]

    # New data for customers builds it again, and the model still sees every table.
    time.sleep(1.1)
    source = next(s["id"] for s in ok(p.api.get(p.dataset))["sources"] if s["table"] == "customers")
    ok(p.api.post(f"{p.dataset}/sources/{source}/replace",
                  files={"file": ("customers.csv", b"customer,segment\nC1,Retail\nC2,Online\nC3,Online\nC4,Retail\n")}))
    again = outputs(p)
    assert again["customers"]["rows"] == 4 and again["customers"]["profile"]["profiled_at"] != after["customers"]["profile"]["profiled_at"]
    assert all(not m["error"] for m in ok(p.api.get(f"{p.dataset}/model"))["measures"])


def test_large_text_columns_check_types_on_a_sample(monkeypatch):
    monkeypatch.setattr(profiling, "SAMPLE_ROWS", 2000)
    con = duckdb.connect()
    con.execute("""CREATE TABLE t AS SELECT
        CASE WHEN i % 100 = 0 THEN 'RM ' || CAST(i AS VARCHAR) ELSE CAST(i AS VARCHAR) END AS amount,
        strftime(DATE '2024-01-01' + CAST(i % 300 AS INTEGER), '%Y-%m-%d') AS day,
        CASE WHEN i % 10 = 0 THEN NULL ELSE 'x' END AS note
        FROM range(20000) t(i)""")
    profile = profiling.profile_table(con, "t")
    amount, day, note = profile["columns"]
    # "RM 100" in 1 row of 100: a decimal stored with currency symbols, counted on the sample and scaled up.
    assert amount["detected_type"] == "decimal" and amount["type_confidence"] == 1.0 and amount["sampled"] == 2000
    symbols = next(i for i in amount["issues"] if "currency symbols" in i)
    assert symbols.startswith("about ") and 100 <= int(symbols.split()[1].replace(",", "")) <= 300
    assert day["detected_type"] == "date" and day["type_confidence"] == 1.0 and day["min"].startswith("2024-01-01")
    assert note["blanks"] == 2000 and note["filled"] == 18000 and note["distinct"] == 1  # counts stay exact
    assert amount["distinct"] == 20000 and amount["unique"]


def test_dashboard_results_are_cached_per_published_version_and_filter(tmp_path, monkeypatch):
    import json as _json

    from test_report import SEGMENT

    from app import report
    p = Project(tmp_path)
    page = p.page()
    ok(p.visual(page, type="bar", title="Revenue by segment", measures=["Revenue"], group_by=SEGMENT))
    computed = []
    real = report.visual_data
    monkeypatch.setattr(report, "visual_data", lambda con, project, visual: computed.append(visual["id"]) or real(con, project, visual))

    first = p.visuals()[0]["data"]
    assert p.visuals()[0]["data"] == first and computed == []  # worked out when it was added, then served from the cache
    chosen = _json.dumps([{"table": "customers", "column": "segment", "operator": "in", "values": ["Retail"]}])
    filtered = ok(p.api.get(p.base, params={"page": page, "filters": chosen}))["pages"][0]["visuals"][0]["data"]
    assert filtered["rows"] == [["Retail", 50.0]] and len(computed) == 1  # a filter is its own entry

    p.publish()  # a new version is new data: worked out again (publishing's own checks aside)
    before = len(computed)
    p.visuals()
    assert len(computed) == before + 1
