"""Data refresh: new data in, the same steps, model and dashboards on top.

A refresh replaces the raw rows of one or more sources while keeping each source's id and
table name, so Transform steps, relationships, measures and dashboards stay attached. Then
everything downstream is re-run and checked against the state before the refresh:

  - row counts (a large drop is suspicious), columns that disappeared or appeared
  - tables whose Applied Steps now fail
  - relationships and measures that broke, and relationship match rates that fell
  - dashboard visuals that would stop working

The outcome is "ok", "warnings" or "failed". With auto-publish on, an ok or warnings refresh
is published at once; a failed one never is, so dashboards keep the last good version.
Google Sheets can be pulled on a schedule; files are refreshed by uploading a replacement.
"""

import copy
from datetime import datetime, timedelta, timezone

from . import activity, ingest, model, profiling, publishing, transform
from .projects import ProjectStore, now, quote

SCHEDULES = {"off": None, "hourly": 1, "every_6_hours": 6, "daily": 24, "weekly": 168}
HISTORY_LIMIT = 50
ROW_DROP = 0.5  # losing more than half the rows is flagged
MATCH_DROP = 0.1  # a relationship losing 10 points of match rate is flagged


class RefreshError(ValueError):
    pass


def settings(project: dict) -> dict:
    return project.setdefault("refresh", {"schedule": "off", "auto_publish": False, "last_run": None, "next_run": None})


def sheet_sources(project: dict) -> list[dict]:
    return [s for s in project["sources"] if s["origin"].get("kind") == "gsheet" and s["origin"].get("url")]


def schedule_next(project: dict, start: datetime | None = None) -> None:
    config = settings(project)
    hours = SCHEDULES.get(config["schedule"])
    if not hours or not sheet_sources(project):
        config["next_run"] = None
        return
    config["next_run"] = ((start or datetime.now(timezone.utc)) + timedelta(hours=hours)).isoformat(timespec="seconds")


def configure(project: dict, schedule: str | None, auto_publish: bool | None) -> dict:
    config = settings(project)
    if schedule is not None:
        if schedule not in SCHEDULES:
            raise RefreshError(f"Schedule must be one of: {', '.join(SCHEDULES)}.")
        if schedule != "off" and not sheet_sources(project):
            raise RefreshError("Only Google Sheets can be pulled on a schedule. Files are refreshed by uploading a replacement.")
        config["schedule"] = schedule
        schedule_next(project)
    if auto_publish is not None:
        config["auto_publish"] = bool(auto_publish)
    return config


def is_due(project: dict, at: datetime) -> bool:
    next_run = settings(project).get("next_run")
    return bool(next_run) and datetime.fromisoformat(next_run) <= at


# Measuring before and after


def measure(con, project: dict) -> dict:
    t = transform.state(project)
    m = model.state(project)
    return {
        "sources": {s["id"]: {"table": s["table"], "rows": s["profile"]["rows"],
                              "columns": [c["name"] for c in s["profile"]["columns"]]} for s in project["sources"]},
        "query_errors": {q["name"]: q["error"] for q in t["queries"] if q["error"]},
        "model_errors": {f"{x['name']} (measure)": x["error"] for x in m["measures"] if x.get("error")}
                        | {f"relationship {r['from_column']} → {r['to_column']}": r["error"]
                           for r in m["relationships"] if r.get("error")},
        "match_rates": {r["id"]: (r.get("checks") or {}).get("match_rate") for r in m["relationships"]
                        if not r.get("error")},
    }


def compare(before: dict, after: dict) -> tuple[list[dict], str]:
    checks = []

    def add(level, text):
        checks.append({"level": level, "text": text})

    for sid, now_ in after["sources"].items():
        old = before["sources"].get(sid)
        if not old:
            continue
        if old["rows"] and now_["rows"] < old["rows"] * (1 - ROW_DROP):
            add("warning", f"{now_['table']}: rows fell from {old['rows']:,} to {now_['rows']:,}.")
        missing = [c for c in old["columns"] if c not in now_["columns"]]
        added = [c for c in now_["columns"] if c not in old["columns"]]
        if missing:
            add("warning", f"{now_['table']}: columns no longer in the data: {', '.join(missing)}.")
        if added:
            add("info", f"{now_['table']}: new columns: {', '.join(added)}.")
    for name, error in after["query_errors"].items():
        if name not in before["query_errors"]:
            add("error", f"Transform {name}: {error}")
    for name, error in after["model_errors"].items():
        if name not in before["model_errors"]:
            add("error", f"Model {name}: {error}")
    for rid, rate in after["match_rates"].items():
        old = before["match_rates"].get(rid)
        if old is not None and rate is not None and rate < old - MATCH_DROP:
            add("warning", f"A relationship's match rate fell from {old:.0%} to {rate:.0%}.")
    status = "failed" if any(c["level"] == "error" for c in checks) else (
        "warnings" if any(c["level"] == "warning" for c in checks) else "ok")
    return checks, status


# Running a refresh


def replace_in(con, project: dict, source: dict, table: ingest.LoadedTable, origin: dict | None = None) -> None:
    """New rows for a source, under the same table name. The caller holds the edit lock."""
    con.register("incoming", table.frame)
    con.execute(f"CREATE OR REPLACE TABLE {quote(source['table'])} AS SELECT * FROM incoming")
    con.unregister("incoming")
    source["profile"] = profiling.profile_table(con, source["table"])
    source["warnings"] = table.warnings
    source["refreshed_at"] = now()
    if origin:
        source["origin"] = {**source["origin"], **origin}


def pick_table(source: dict, tables: list[ingest.LoadedTable]) -> ingest.LoadedTable:
    """The table in a replacement file that matches the source: the only one, or the same Excel sheet."""
    if len(tables) == 1:
        return tables[0]
    sheet = source["origin"].get("sheet")
    for table in tables:
        if sheet and table.sheet == sheet:
            return table
    for table in tables:
        if table.name == source["origin"].get("label"):
            return table
    sheets = ", ".join(t.sheet or t.name for t in tables)
    raise RefreshError(f"The file has several sheets ({sheets}) and none matches {source['table']}.")


def run(store: ProjectStore, boards, project_id: str, new_data: dict[str, tuple[ingest.LoadedTable, dict | None]],
        failures: dict[str, str], trigger: str, actor: str = "You") -> dict:
    """Apply new data, re-run and check everything, record the result, and auto-publish if allowed.

    `new_data` maps source id to (table, origin update); `failures` maps source id to why it got no new data.
    """
    with store.edit(project_id) as project:
        with store.connect(project_id) as con:
            transform.apply_all(con, project)
            model.revalidate(con, project)
            before = measure(con, project)
            sources = {s["id"]: s for s in project["sources"]}
            for sid, (table, origin) in new_data.items():
                replace_in(con, project, sources[sid], table, origin)
            transform.apply_all(con, project)
            model.revalidate(con, project)
            after = measure(con, project)
            checks, status = compare(before, after)
            for sid, reason in failures.items():
                checks.insert(0, {"level": "error", "text": f"{sources[sid]['table']}: {reason}"})
            if failures and not new_data:
                status = "failed"
            elif failures and status == "ok":
                status = "warnings"
            broken = publishing.broken_visuals(con, project, boards.using(project_id))
            for problem in broken:
                checks.append({"level": "error", "text": f"Dashboard {problem['dashboard']} ({problem['version']}): "
                                                          f"{problem['visual']}: {problem['error']}"})
            if broken:
                status = "failed"

        entry = {
            "at": now(),
            "trigger": trigger,
            "status": status,
            "sources": [
                {"table": sources[sid]["table"], "rows_before": before["sources"][sid]["rows"],
                 "rows_after": after["sources"][sid]["rows"]}
                for sid in new_data
            ],
            "failed_sources": [sources[sid]["table"] for sid in failures],
            "checks": checks,
            "published_version": None,
        }
        config = settings(project)
        if new_data and config["auto_publish"] and status != "failed":
            published = publishing.publish(store, project)
            entry["published_version"] = published["version"]
        history = project.setdefault("refreshes", [])
        history.insert(0, entry)
        del history[HISTORY_LIMIT:]
        if trigger == "schedule":
            config["last_run"] = entry["at"]
            # Keep the rhythm: the next run counts from the slot that was due, not from when it finished.
            due = datetime.fromisoformat(config["next_run"]) if config.get("next_run") else datetime.now(timezone.utc)
            schedule_next(project, due)
            if config["next_run"] and datetime.fromisoformat(config["next_run"]) <= datetime.now(timezone.utc):
                schedule_next(project)  # far behind (the app was off): start again from now
        tables = ", ".join(s["table"] for s in entry["sources"]) or "nothing"
        published_note = f"; published v{entry['published_version']}" if entry["published_version"] else ""
        activity.log(project, "refresh", f"Refreshed {tables}: {status}{published_note}", actor)
    if entry["published_version"]:
        store.remove_old_copies(project_id, keep=f"published-v{entry['published_version']}.duckdb")
    return entry


def pull_sheets(store: ProjectStore, boards, project_id: str, fetch, trigger: str = "manual", actor: str = "You",
                only: str | None = None) -> dict:
    """Download every Google Sheet source again (or just `only`) and run a refresh with the results."""
    project = store.get(project_id)
    targets = [s for s in sheet_sources(project) if only is None or s["id"] == only]
    if not targets:
        raise RefreshError("This dataset has no Google Sheets to pull. Upload a replacement file instead.")
    new_data, failures = {}, {}
    for source in targets:
        try:
            # Downloads happen before the dataset is locked, so a slow sheet never blocks other changes.
            new_data[source["id"]] = (ingest.read_gsheet(source["origin"]["url"], fetch, source["table"]), None)
        except ingest.IngestError as exc:
            failures[source["id"]] = str(exc)
    return run(store, boards, project_id, new_data, failures, trigger, actor)


def replace_file(store: ProjectStore, boards, project_id: str, source_id: str, filename: str, data: bytes) -> dict:
    project = store.get(project_id)
    source = next((s for s in project["sources"] if s["id"] == source_id), None)
    if source is None:
        raise RefreshError("Source not found.")
    tables = ingest.read_file(filename, data)
    table = pick_table(source, tables)
    kind = "excel" if filename.lower().endswith(ingest.EXCEL_SUFFIXES) else "csv"
    origin = {"kind": kind, "file": filename, **({"sheet": table.sheet} if table.sheet else {})}
    return run(store, boards, project_id, {source_id: (table, origin)}, {}, "replace")


def run_due(store: ProjectStore, boards, fetch, at: datetime | None = None) -> list[str]:
    """Pull the sheets of every dataset whose schedule is due. Returns the dataset ids refreshed."""
    at = at or datetime.now(timezone.utc)
    done = []
    for project in store.all():
        if is_due(project, at):
            try:
                pull_sheets(store, boards, project["id"], fetch, "schedule", "Schedule")
            except RefreshError:
                with store.edit(project["id"]) as fresh:
                    schedule_next(fresh, at)
            done.append(project["id"])
    return done


def history_view(project: dict) -> dict:
    config = copy.deepcopy(settings(project))
    return {
        "settings": config,
        "schedules": list(SCHEDULES),
        "history": project.get("refreshes", []),
        "sources": [
            {"id": s["id"], "table": s["table"], "kind": s["origin"].get("kind"), "file": s["origin"].get("file"),
             "url": s["origin"].get("url"), "rows": s["profile"]["rows"], "refreshed_at": s.get("refreshed_at") or s["added_at"]}
            for s in project["sources"]
        ],
    }
