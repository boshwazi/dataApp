"""Bringing a Power BI model into a dataset: tables, the date table, relationships and measures.

From a .pbix the data comes too, so the dataset is complete at once. From a .pbip there is no
data: the model waits in the dataset (project["powerbi"]["pending"]) until each table's data is
loaded under the same name, then "apply" adds the relationships and measures.

Everything goes through the engine's normal checks (relationship match rates, measure checks),
and the import report says what came across, what was split, and what was left behind and why.
"""

import copy
from dataclasses import asdict

import pandas as pd

from . import model, pbi_dax, transform
from .pbi_model import AUTO_DATE, PbiModel, PbiTable
from .projects import now, table_name

DATE_NAMES = {"date", "dates", "calendar", "dimdate", "dim date", "date table", "dim_date"}
SQL_TYPES = {"integer": "BIGINT", "decimal": "DOUBLE", "boolean": "BOOLEAN", "text": "VARCHAR"}


def is_date_table(table: PbiTable, model_: PbiModel) -> bool:
    """Power BI's own date table, which datawazi replaces with its date table."""
    if table.is_date_table:
        return True
    if table.name.lower() not in DATE_NAMES:
        return False
    targets = {(r.to_table.lower(), r.to_column.lower()) for r in model_.relationships}
    if any(c.type == "date" and (table.name.lower(), c.name.lower()) in targets for c in table.columns):
        return True
    # A table called Date or Calendar with one row per day is a date table even if nothing links to it yet.
    if table.data is not None:
        for c in table.columns:
            if c.type == "date" and c.name in table.data and table.data[c.name].is_unique and len(table.data) >= 28:
                return True
    return False


def is_measure_table(table: PbiTable, model_: PbiModel) -> bool:
    """A table that only holds measures (often '_Measures' with one placeholder column)."""
    homes = {m.table for m in model_.measures}
    return table.name in homes and len(table.columns) <= 1 and not any(
        r.from_table == table.name or r.to_table == table.name for r in model_.relationships)


def frame_and_types(table: PbiTable) -> tuple[pd.DataFrame, dict[str, str]]:
    """The table's data with the column types to load it as. Dates without times load as DATE."""
    frame = table.data.copy()
    types = {}
    by_name = {c.name: c for c in table.columns}
    for name in frame.columns:
        column = by_name.get(name)
        kind = column.type if column else "text"
        if kind == "date":
            values = pd.to_datetime(frame[name], errors="coerce")
            has_time = bool(((values.dt.hour != 0) | (values.dt.minute != 0) | (values.dt.second != 0)).fillna(False).any())
            frame[name] = values
            types[name] = "TIMESTAMP" if has_time else "DATE"
        else:
            types[name] = SQL_TYPES.get(kind, "VARCHAR")
    return frame, types


def plan(model_: PbiModel) -> dict:
    """What an import will do, before anything is written: tables to load, skip or wait for."""
    tables = []
    for t in model_.tables:
        if is_date_table(t, model_):
            role = "date"
        elif is_measure_table(t, model_):
            role = "measures"
        elif t.data is not None:
            role = "load"
        else:
            role = "needs_data"
        why = None
        if role == "needs_data":
            why = ("DirectQuery: Power BI queries the source live and keeps no data in the file."
                   if t.mode == "directquery" else
                   "Direct Lake: the data stays in Fabric." if t.mode == "directlake" else
                   "A Power BI project holds the model but not the data.")
        tables.append({"name": t.name, "role": role, "columns": len(t.columns),
                       "rows": None if t.data is None else int(len(t.data)), "calculated": bool(t.calculated),
                       "source_hint": source_hint(t.source_m), "connector": t.connector, "mode": t.mode,
                       "refreshed_at": t.refreshed_at, "why": why})
    return {"file": model_.name, "kind": model_.kind, "tables": tables,
            "measures": len(model_.measures), "relationships": len(model_.relationships), "roles": len(model_.roles)}


def source_hint(m: str | None) -> str | None:
    """Where Power Query loads a table from, in plain words (for .pbip, to find the right file)."""
    if not m:
        return None
    import re
    sheet = re.search(r'\[Item="([^"]+)",\s*Kind="(Sheet|Table)"\]', m)
    workbook = re.search(r"File\.Contents\(\s*\"([^\"]+)\"", m)
    if "Excel.Workbook" in m:
        where = f"sheet '{sheet.group(1)}'" if sheet else "an Excel sheet"
        return f"{where} of {workbook.group(1).split(chr(92))[-1]}" if workbook else f"{where} of an Excel file"
    if "Csv.Document" in m:
        return f"the CSV file {workbook.group(1).split(chr(92))[-1]}" if workbook else "a CSV file"
    if "Sql.Database" in m:
        return "a SQL database"
    if "SharePoint" in m:
        return "a SharePoint file"
    return "a Power Query source"


def describe_for_storage(model_: PbiModel) -> dict:
    """The model without its data, kept in the dataset until its tables are loaded (.pbip)."""
    tables = [{**asdict(t), "data": None} for t in model_.tables]
    return {"name": model_.name, "kind": model_.kind, "tables": tables,
            "measures": [asdict(m) for m in model_.measures],
            "relationships": [asdict(r) for r in model_.relationships], "roles": model_.roles}


def from_storage(stored: dict) -> PbiModel:
    from .pbi_model import PbiColumn, PbiMeasure, PbiRelationship
    tables = [PbiTable(**{**{k: v for k, v in t.items() if k != "columns"},
                          "columns": [PbiColumn(**c) for c in t["columns"]]}) for t in stored["tables"]]
    return PbiModel(name=stored["name"], kind=stored["kind"], tables=tables,
                    measures=[PbiMeasure(**m) for m in stored["measures"]],
                    relationships=[PbiRelationship(**r) for r in stored["relationships"]], roles=stored.get("roles", []))


def apply_model(con, project: dict, model_: PbiModel, names: dict[str, str]) -> dict:
    """Add the date table, relationships and measures for the tables that are loaded.

    `names` maps a Power BI table name to the dataset's table name. Returns the import report."""
    report = {"relationships": [], "measures": [], "split": [], "not_converted": [], "skipped_tables": [], "notes": []}
    transform.apply_all(con, project)
    found = model.tables(con, project)
    date_tables = {t.name for t in model_.tables if is_date_table(t, model_)}
    auto_dates = any(r.to_table == AUTO_DATE for r in model_.relationships)
    if auto_dates:
        date_tables.add(AUTO_DATE)

    if date_tables:
        # Keep the Power BI date table's years when it had data; otherwise they follow the loaded dates.
        years = [pd.to_datetime(t.data[c.name], errors="coerce").dt.year for t in model_.tables if t.name in date_tables
                 and t.data is not None for c in t.columns if c.type == "date" and c.name in t.data]
        start = int(min(y.min() for y in years)) if years else None
        end = int(max(y.max() for y in years)) if years else None
        replaced = ", ".join(sorted(t for t in date_tables if t != AUTO_DATE)) or "Power BI's automatic date tables"
        model.set_date_table(con, project, start_year=start, end_year=end, reason=f"Replaces {replaced} from Power BI.")
        found = model.tables(con, project)
        shown = sorted(t for t in date_tables if t != AUTO_DATE) or ["Power BI's automatic date tables"]
        report["notes"].append(f"The date table ({', '.join(shown)}) is replaced by datawazi's Date table, "
                               "which has the same kind of columns (Year, Quarter, Month...).")
    for name in date_tables:
        names[name] = model.DATE_NAME

    # Relationships, through the engine's checks.
    existing = {(r["from_table"], r["from_column"], r["to_table"], r["to_column"]) for r in model.state(project)["relationships"]}
    for r in model_.relationships:
        label = f"{r.from_table}[{r.from_column}] → {'Date' if r.to_table == AUTO_DATE else r.to_table}[{r.to_column}]"
        if r.from_table not in names or r.to_table not in names:
            report["relationships"].append({"relationship": label, "added": False, "why": "One of its tables isn't loaded yet."})
            continue
        to_column = "Date" if r.to_table in date_tables else r.to_column
        proposal = {"from_table": names[r.from_table], "from_column": r.from_column,
                    "to_table": names[r.to_table], "to_column": to_column,
                    "reason": "Imported from Power BI."}
        try:
            added = model.add_relationship(con, project, proposal, "Approved")
            note = None
            if r.both_directions:
                note = "Power BI filtered both ways; datawazi filters from the one side to the many side."
            if not r.active:
                note = "Inactive in Power BI." + (f" {note}" if note else "")
            report["relationships"].append({"relationship": label, "added": True, "active": added.get("active"),
                                            "match": (added.get("checks") or {}).get("match_rate"), "note": note})
        except model.ModelError as exc:
            report["relationships"].append({"relationship": label, "added": False, "why": str(exc)})
    found = model.tables(con, project)
    if date_tables:
        linked = {(r["from_table"], r["from_column"]) for r in model.state(project)["relationships"]
                  if r["to_table"] == model.DATE_ID}
        unlinked = [f"{t.name}[{c.name}]" for t in found.values() if t.id != model.DATE_ID
                    for c in t.columns if c.type == "date" and (t.id, c.name) not in linked]
        if unlinked and not linked:
            report["notes"].append(f"Nothing is linked to the Date table yet (it wasn't in Power BI either). Link "
                                   f"{' or '.join(unlinked[:3])} to Date[Date] in Model, or ask the Model AI to, "
                                   "so measures can be split by year and month.")

    # Measures: translate in passes, since measures refer to each other in any order.
    loaded_columns = {pbi: [c.name for c in found[_id_of(found, ours)].columns]
                      for pbi, ours in names.items() if _id_of(found, ours)}
    ctx = pbi_dax.Context(tables=loaded_columns, measures={})
    existing_names = {m["name"].lower() for m in model.state(project)["measures"]}
    pending = [m for m in model_.measures if m.name.lower() not in existing_names]
    for _ in range(len(pending) + 1):
        progress, left = False, []
        for m in pending:
            try:
                proposal, helpers = pbi_dax.to_measure(m.name, m.expression, ctx)
            except pbi_dax.Untranslatable as exc:
                left.append((m, str(exc)))
                continue
            try:
                for helper in helpers:
                    _add_measure(con, project, found, helper, names, None)
                    ctx.measures[helper["name"].lower()] = helper
                    report["split"].append({"measure": m.name, "helper": helper["name"]})
                _add_measure(con, project, found, {**proposal, "description": m.description}, names, m.format_string)
                ctx.measures[m.name.lower()] = proposal
                report["measures"].append({"measure": m.name, "dax": m.expression})
                progress = True
            except model.ModelError as exc:
                left.append((m, str(exc)))
        pending = [m for m, _ in left]
        if not progress:
            break
    for m, why in left:
        report["not_converted"].append({"measure": m.name, "dax": m.expression, "why": why})
    model.revalidate(con, project)
    return report


def _id_of(found: dict, name: str) -> str | None:
    for table_id, table in found.items():
        if table.name.lower() == str(name).lower():
            return table_id
    return None


def _add_measure(con, project, found, proposal: dict, names: dict[str, str], format_string: str | None) -> None:
    proposal = copy.deepcopy(proposal)
    if proposal["kind"] == "aggregate":
        ours = names.get(proposal["table"])
        if not ours:
            raise model.ModelError(f"Its table {proposal['table']} isn't loaded yet.")
        proposal["table"] = ours
    proposal["format"] = pbi_dax.format_from(format_string)
    proposal["reason"] = "Imported from Power BI."
    model.add_measure(con, project, proposal, "Approved")


def origin_for(table: PbiTable, file: str) -> dict:
    """How a source imported from Power BI is labelled: the file, the original connector and the snapshot date."""
    return {"kind": "powerbi", "file": file, "label": table.name, "connector": table.connector,
            "snapshot": table.refreshed_at, "mode": table.mode}


def snapshot_note(model_: PbiModel, loaded: list[str]) -> str | None:
    """Imported data is a copy of Power BI's last refresh; say so, and how to refresh it."""
    dates = sorted(t.refreshed_at for t in model_.tables if t.name in loaded and t.refreshed_at)
    if model_.kind != "pbix" or not loaded:
        return None
    when = f" (Power BI last refreshed it on {dates[-1][:10]})" if dates else ""
    return (f"The data is a snapshot of the file{when}. datawazi can't run Power BI's connectors, so to update "
            "it, refresh the file in Power BI Desktop and use Refresh from a newer .pbix.")


def refresh_from(store, boards, project_id: str, model_: PbiModel) -> dict:
    """New data for tables imported from Power BI, from a newer .pbix: matched by table name and run
    through the normal refresh checks and history. Measures and relationships are kept, not re-imported."""
    from . import ingest, refresh  # refresh uses the stores; imported here to keep this module light

    project = store.get(project_id)
    imported = [s for s in project["sources"] if s["origin"].get("kind") == "powerbi"]
    if not imported:
        raise model.ModelError("Nothing in this dataset was imported from Power BI.")
    new_data, failures = {}, {}
    for source in imported:
        table = model_.table(source["origin"].get("label") or source["table"])
        if table is None:
            failures[source["id"]] = f"{source['origin'].get('label')} isn't in {model_.name}."
        elif table.data is None:
            failures[source["id"]] = f"{table.name} has no data in {model_.name} ({table.mode})."
        else:
            frame, types = frame_and_types(table)
            loaded = ingest.LoadedTable(name=table.name, frame=frame, types=types)
            new_data[source["id"]] = (loaded, origin_for(table, model_.name))
    entry = refresh.run(store, boards, project_id, new_data, failures, "powerbi")
    labels = {s["origin"].get("label", "").lower() for s in imported}
    entry["not_added"] = [t.name for t in model_.tables if t.data is not None and t.name.lower() not in labels
                          and not is_date_table(t, model_) and not is_measure_table(t, model_)]
    return entry


def record(project: dict, model_: PbiModel, report: dict | None, waiting: list[str]) -> dict:
    """Keep what was imported on the dataset: the report, and the model while tables still need data."""
    entry = {"file": model_.name, "kind": model_.kind, "imported_at": now(), "report": report,
             "waiting_for": waiting, "pending": describe_for_storage(model_) if waiting else None}
    project.setdefault("powerbi", {}).update(entry)
    return entry


def dataset_name(table: str) -> str:
    return table_name(table)
