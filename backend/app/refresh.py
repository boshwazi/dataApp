"""Data refresh: new data in, the same steps, model and dashboards on top.

A refresh replaces the raw rows of one or more sources while keeping each source's id and
table name, so Transform steps, relationships, measures and dashboards stay attached. (A source
set to append adds the new rows instead, optionally skipping rows whose key is already loaded.)
Then everything downstream is re-run and checked against the state before the refresh:

  - row counts (a large drop is suspicious), columns that disappeared or appeared
  - tables whose Applied Steps now fail
  - relationships and measures that broke, and relationship match rates that fell
  - dashboard visuals that would stop working

The outcome is "ok", "warnings" or "failed". With auto-publish on, an ok or warnings refresh
is published at once; a failed one never is, so dashboards keep the last good version.

Google Sheets, files behind shared links (OneDrive, SharePoint, Google Drive) and tables linked
from other datasets can be pulled on a schedule: every hour or 6 hours, or at a time of day in
Malaysia time (daily, weekdays or weekly). The server runs scheduled pulls as background jobs; a
pull that couldn't download is retried a few times, and one that still fails (or passes with
warnings) raises an alert. Uploaded files are refreshed by uploading a replacement. After a
dataset publishes, the datasets linking its tables pull them again, and dashboard insights are
re-checked against the new numbers (and rewritten by the AI where they no longer hold).
"""

import contextvars
import copy
import logging
import re
from datetime import datetime, timedelta, timezone

from . import activity, ingest, links, model, profiling, publishing, report, transform
from .projects import ProjectStore, load_sql, now, quote

log = logging.getLogger(__name__)

MYT = timezone(timedelta(hours=8))  # Malaysia time: UTC+8 all year
INTERVALS = {"hourly": 1, "every_6_hours": 6}
TIMED = ("daily", "weekdays", "weekly")
SCHEDULES = ("off", *INTERVALS, *TIMED)
DEFAULTS = {"schedule": "off", "auto_publish": False, "last_run": None, "next_run": None,
            "at": "07:00", "day": 0, "follow_links": True, "retry": 0}
RETRY_MINUTES = (5, 15, 45)  # waits before each retry of a scheduled pull that couldn't download
UNATTENDED = ("schedule", "linked")  # refreshes nobody is watching: these raise alerts
HISTORY_LIMIT = 50
ROW_DROP = 0.5  # losing more than half the rows is flagged
MATCH_DROP = 0.1  # a relationship losing 10 points of match rate is flagged
AT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# The datasets a chain of linked refreshes has passed through, so datasets linking each other stop.
CHAIN: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("refresh_chain", default=())


class RefreshError(ValueError):
    pass


def settings(project: dict) -> dict:
    config = project.setdefault("refresh", {})
    for key, value in DEFAULTS.items():
        config.setdefault(key, value)
    return config


def pullable(source: dict) -> bool:
    """Whether the app can fetch this source again by itself."""
    origin = source["origin"]
    return (origin.get("kind") in ("gsheet", "link") and bool(origin.get("url"))) or origin.get("kind") == "dataset"


def sheet_sources(project: dict) -> list[dict]:
    """Sources the app can pull again by itself: Google Sheets, shared-link files and linked tables."""
    return [s for s in project["sources"] if pullable(s)]


def next_slot(config: dict, after: datetime) -> datetime | None:
    """The next scheduled time after `after`, in UTC, or None when the schedule is off."""
    schedule = config["schedule"]
    if schedule in INTERVALS:
        return after + timedelta(hours=INTERVALS[schedule])
    if schedule not in TIMED:
        return None
    hour, minute = (int(x) for x in config["at"].split(":"))
    local = after.astimezone(MYT)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    for _ in range(8):
        day_fits = (schedule == "daily" or (schedule == "weekdays" and candidate.weekday() < 5)
                    or (schedule == "weekly" and candidate.weekday() == config["day"]))
        if candidate > local and day_fits:
            return candidate.astimezone(timezone.utc)
        candidate += timedelta(days=1)
    return None


def schedule_next(project: dict, start: datetime | None = None) -> None:
    config = settings(project)
    slot = next_slot(config, start or datetime.now(timezone.utc)) if sheet_sources(project) else None
    config["next_run"] = slot.isoformat(timespec="seconds") if slot else None


def configure(project: dict, schedule: str | None, auto_publish: bool | None, at: str | None = None,
              day: int | None = None, follow_links: bool | None = None) -> dict:
    config = settings(project)
    if at is not None:
        if not AT.match(at):
            raise RefreshError("Give the time as HH:MM, for example 07:30.")
        config["at"] = at
    if day is not None:
        if day not in range(7):
            raise RefreshError("The day is 0 (Monday) to 6 (Sunday).")
        config["day"] = day
    if schedule is not None:
        if schedule not in SCHEDULES:
            raise RefreshError(f"Schedule must be one of: {', '.join(SCHEDULES)}.")
        if schedule != "off" and not sheet_sources(project):
            raise RefreshError("Only Google Sheets, files from shared links and linked tables can be pulled on a "
                               "schedule. Uploaded files are refreshed by uploading a replacement.")
        config["schedule"] = schedule
    if schedule is not None or at is not None or day is not None:
        config["retry"] = 0
        schedule_next(project)
    if auto_publish is not None:
        config["auto_publish"] = bool(auto_publish)
    if follow_links is not None:
        config["follow_links"] = bool(follow_links)
    return config


def set_load(project: dict, source_id: str, mode: str, key: str | None) -> dict:
    """Replace each refresh's rows (the default) or append them, optionally skipping rows whose key is loaded."""
    source = next((s for s in project["sources"] if s["id"] == source_id), None)
    if source is None:
        raise RefreshError("Source not found.")
    if mode not in ("replace", "append"):
        raise RefreshError("Load mode is replace or append.")
    columns = [c["name"] for c in source["profile"]["columns"]]
    if mode == "append" and key and key not in columns:
        raise RefreshError(f"{source['table']} has no column '{key}'. Columns: {', '.join(columns)}.")
    source["load"] = {"mode": mode, "key": (key or None) if mode == "append" else None}
    return source


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


def replace_in(con, project: dict, source: dict, table: ingest.LoadedTable, origin: dict | None = None) -> str | None:
    """New rows for a source, under the same table name. The caller holds the edit lock.
    Returns a note for the refresh's checks when rows were appended."""
    load = source.get("load") or {}
    con.register("incoming", table.frame)
    try:
        if load.get("mode") == "append":
            note = append_rows(con, source, table, load.get("key"))
        else:
            con.execute(f"CREATE OR REPLACE TABLE {quote(source['table'])} AS {load_sql(table.types)}")
            note = None
    finally:
        con.unregister("incoming")
    source["profile"] = profiling.profile_table(con, source["table"])
    source["warnings"] = table.warnings
    source["refreshed_at"] = now()
    if origin:
        source["origin"] = {**source["origin"], **origin}
    return note


def append_rows(con, source: dict, table: ingest.LoadedTable, key: str | None) -> str:
    name, staged = quote(source["table"]), "__datawazi_incoming"
    con.execute(f"CREATE OR REPLACE TEMP TABLE {staged} AS {load_sql(table.types)}")
    try:
        have = [r[0] for r in con.execute(f"DESCRIBE {name}").fetchall()]
        new = [r[0] for r in con.execute(f"DESCRIBE {staged}").fetchall()]
        extra = [c for c in new if c not in have]
        if extra:
            raise RefreshError(f"The new data has columns {source['table']} doesn't: {', '.join(extra)}. Appending "
                               "needs the same columns; set the table to replace its rows to take them.")
        where = ""
        if key:
            if key not in new:
                raise RefreshError(f"The new data has no {key} column to match rows by.")
            k = quote(key)
            where = f" WHERE {k} IS NULL OR {k} NOT IN (SELECT {k} FROM {name} WHERE {k} IS NOT NULL)"
        incoming = con.execute(f"SELECT count(*) FROM {staged}").fetchone()[0]
        before = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        con.execute(f"INSERT INTO {name} BY NAME SELECT * FROM {staged}{where}")
        added = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0] - before
    finally:
        con.execute(f"DROP TABLE IF EXISTS {staged}")
    skipped = incoming - added
    return (f"{source['table']}: appended {added:,} new rows"
            + (f", skipped {skipped:,} already loaded (same {key})" if skipped else "") + ".")


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
    new_data, failures, notes = dict(new_data), dict(failures), []
    with store.edit(project_id) as project:
        with store.connect(project_id) as con:
            transform.apply_all(con, project)
            model.revalidate(con, project)
            before = measure(con, project)
            sources = {s["id"]: s for s in project["sources"]}
            for sid, (table, origin) in list(new_data.items()):
                try:
                    note = replace_in(con, project, sources[sid], table, origin)
                except RefreshError as exc:  # this source's new rows don't fit; the others still load
                    failures[sid] = str(exc)
                    del new_data[sid]
                    continue
                if note:
                    notes.append({"level": "info", "text": note})
            transform.apply_all(con, project)
            model.revalidate(con, project)
            after = measure(con, project)
            checks, status = compare(before, after)
            checks = notes + checks
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
        if trigger == "schedule":
            config["last_run"] = entry["at"]
            plan_next(project, entry, status, failures)
        if trigger in UNATTENDED and status != "ok" and not entry.get("retry_at"):
            problem = next((c["text"] for c in checks if c["level"] == "error"), None) or next(
                (c["text"] for c in checks if c["level"] == "warning"), "")
            project["refresh_alert"] = {"at": entry["at"], "status": status, "trigger": trigger, "text": problem}
        elif status == "ok":
            project.pop("refresh_alert", None)
        history = project.setdefault("refreshes", [])
        history.insert(0, entry)
        del history[HISTORY_LIMIT:]
        tables = ", ".join(s["table"] for s in entry["sources"]) or "nothing"
        published_note = f"; published v{entry['published_version']}" if entry["published_version"] else ""
        activity.log(project, "refresh", f"Refreshed {tables}: {status}{published_note}", actor)
    if entry["published_version"]:
        store.remove_old_copies(project_id, keep=publishing.kept_files(project))
        store.published(project_id)
    return entry


def plan_next(project: dict, entry: dict, status: str, failures: dict) -> None:
    """After a scheduled pull: retry soon if it couldn't download, otherwise wait for the next slot."""
    config = settings(project)
    current = datetime.now(timezone.utc)
    if status == "failed" and failures and config["retry"] < len(RETRY_MINUTES):
        wait = RETRY_MINUTES[config["retry"]]
        config["retry"] += 1
        config["next_run"] = entry["retry_at"] = (current + timedelta(minutes=wait)).isoformat(timespec="seconds")
        return
    config["retry"] = 0
    if config["schedule"] in INTERVALS:
        # Keep the rhythm: the next run counts from the slot that was due, not from when it finished.
        due_at = datetime.fromisoformat(config["next_run"]) if config.get("next_run") else current
        schedule_next(project, due_at)
        if config["next_run"] and datetime.fromisoformat(config["next_run"]) <= current:
            schedule_next(project)  # far behind (the app was off): start again from now
    else:
        schedule_next(project)


def pull_sheets(store: ProjectStore, boards, project_id: str, fetch, trigger: str = "manual", actor: str = "You",
                only: str | None = None, source_ids: list[str] | None = None) -> dict:
    """Fetch every pullable source again (or just `only`, or `source_ids`) and run a refresh with the results."""
    project = store.get(project_id)
    wanted = {only} if only else set(source_ids) if source_ids is not None else None
    targets = [s for s in sheet_sources(project) if wanted is None or s["id"] in wanted]
    if not targets:
        raise RefreshError("This dataset has no Google Sheets, shared links or linked tables to pull. "
                           "Upload a replacement file instead.")
    new_data, failures = {}, {}
    for source in targets:
        origin = source["origin"]
        try:
            # Downloads happen before the dataset is locked, so a slow download never blocks other changes.
            if origin.get("kind") == "dataset":
                new_data[source["id"]] = links.read(store, origin["dataset_id"], origin["query_id"])
            elif origin.get("kind") == "link":
                tables, filename = ingest.read_shared_file(origin["url"], fetch)
                new_data[source["id"]] = (pick_table(source, tables), {"file": filename} if filename else None)
            else:
                new_data[source["id"]] = (ingest.read_gsheet(origin["url"], fetch, source["table"]), None)
        except (ingest.IngestError, links.LinkError, RefreshError) as exc:
            failures[source["id"]] = str(exc)
    return run(store, boards, project_id, new_data, failures, trigger, actor)


def pull_linked(store: ProjectStore, boards, project_id: str, from_dataset: str, fetch) -> dict | None:
    """After `from_dataset` publishes: pull this dataset's tables linked from it, if they're behind."""
    project = store.get(project_id)
    versions = links.latest_versions(store)
    behind = [s["id"] for s in project["sources"]
              if s["origin"].get("dataset_id") == from_dataset and links.update_available(s, versions)]
    if not behind or not settings(project)["follow_links"]:
        return None
    return pull_sheets(store, boards, project_id, fetch, "linked", "Linked table", source_ids=behind)


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


def due(store: ProjectStore, at: datetime | None = None) -> list[str]:
    at = at or datetime.now(timezone.utc)
    return [p["id"] for p in store.all() if is_due(p, at)]


def run_scheduled(store: ProjectStore, boards, project_id: str, fetch, at: datetime | None = None) -> None:
    try:
        pull_sheets(store, boards, project_id, fetch, "schedule", "Schedule")
    except RefreshError:
        with store.edit(project_id) as fresh:
            schedule_next(fresh, at)


def run_due(store: ProjectStore, boards, fetch, at: datetime | None = None) -> list[str]:
    """Pull the sources of every dataset whose schedule is due, one after another. Returns the dataset ids.
    (The server's scheduler runs each one as its own background job instead; see main.py.)"""
    done = due(store, at)
    for project_id in done:
        run_scheduled(store, boards, project_id, fetch, at)
    return done


# Insights after new data


def recheck_insights(store: ProjectStore, boards, dataset_id: str, rewrite=None) -> int:
    """Check every dashboard insight on this dataset against its newly published numbers.

    Insights that were verified and no longer match are marked stale; with `rewrite(con, project, pages,
    dashboard)` (the Report AI), the pages holding them are written again. Returns how many went stale."""
    dataset = store.get(dataset_id)
    frozen = publishing.published_project(dataset)
    if frozen is None:
        return 0
    stale_total = 0
    with store.connect_published(dataset_id, dataset["published"]["file"]) as con:
        for dash in boards.using(dataset_id):
            changed = {}  # (side, visual id) -> insight; (side, page id) -> summary
            for side in ("draft", "published"):
                # Work on a copy, outside the dashboard's lock: the AI can take a while.
                pages = copy.deepcopy((dash.get(side) or {}).get("pages") or [])
                project = publishing.bind(frozen, {"pages": pages, "history": []})
                stale_pages = []
                for page in pages:
                    stale = False
                    for visual in page["visuals"]:
                        insight = visual.get("insight")
                        if not insight or not insight.get("text"):
                            continue
                        try:
                            check = report.check_insight(insight["text"], report.visual_data(con, project, visual))
                        except (report.ReportError, model.ModelError):
                            continue
                        was = (insight.get("check") or {}).get("verified")
                        insight["check"] = check
                        if was and not check["verified"]:
                            insight["stale"] = True
                            stale = True
                            stale_total += 1
                        changed[(side, visual["id"])] = insight
                    if stale:
                        stale_pages.append(page)
                if stale_pages and rewrite is not None:
                    try:
                        rewrite(con, project, stale_pages, dash)
                    except Exception:  # noqa: BLE001  the stale marks stay; the next refresh tries again
                        log.exception("Rewriting insights for dashboard %s failed", dash["id"])
                    for page in stale_pages:
                        changed[(side, page["id"])] = page.get("summary")
                        for visual in page["visuals"]:
                            if visual.get("insight"):
                                changed[(side, visual["id"])] = visual["insight"]
            if changed:
                with boards.edit(dash["id"]) as fresh:
                    for side in ("draft", "published"):
                        for page in (fresh.get(side) or {}).get("pages") or []:
                            if (side, page["id"]) in changed:
                                page["summary"] = changed[(side, page["id"])]
                            for visual in page["visuals"]:
                                if (side, visual["id"]) in changed and visual.get("insight"):
                                    visual["insight"] = changed[(side, visual["id"])]
    return stale_total


def history_view(project: dict) -> dict:
    config = copy.deepcopy(settings(project))
    return {
        "settings": config,
        "schedules": list(SCHEDULES),
        "history": project.get("refreshes", []),
        "alert": project.get("refresh_alert"),
        "versions": publishing.versions(project),
        "sources": [
            {"id": s["id"], "table": s["table"], "kind": s["origin"].get("kind"), "file": s["origin"].get("file"),
             "url": s["origin"].get("url"), "provider": s["origin"].get("provider"), "rows": s["profile"]["rows"],
             "refreshed_at": s.get("refreshed_at") or s["added_at"], "pullable": pullable(s),
             "load": s.get("load") or {"mode": "replace", "key": None},
             "columns": [c["name"] for c in s["profile"]["columns"]]}
            for s in project["sources"]
        ],
    }
