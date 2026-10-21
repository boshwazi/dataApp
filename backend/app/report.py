"""The Report stage: pages of visuals computed from the model's measures.

Visual types: KPI cards (value, change vs the same period last year, 12-month sparkline,
top driver), sorted horizontal bars, columns for ordered categories, stacked bars and
columns, lines and areas for trends, pies and donuts, scatter plots and detail tables.

The house style (sorted bars for comparisons, pies and donuts only for a few slices) is
advice, not a gate: when a user asks for a chart the style would not pick, it is built and
carries a short tip. Only charts the data cannot support are refused. No gauges and no dual
axes. A page holds at most 7 KPI cards.

Every number comes from model.evaluate. Breakdowns of additive measures are checked to
add up to the total, and numbers quoted in AI insights are checked against the visual's
data; anything that cannot be confirmed is flagged rather than shown as fact.
"""

import copy
import math
import re
from datetime import date, timedelta

from . import model
from .projects import new_id, now

HISTORY_LIMIT = 30
VISUAL_TYPES = ("kpi", "bar", "column", "stacked_bar", "stacked_column", "line", "area", "pie", "donut", "scatter", "table")
SHARE = ("pie", "donut")
STACKED = ("stacked_bar", "stacked_column")
MAX_KPIS = 7
MAX_VISUALS = 10
MAX_BARS = 10
MAX_TABLE_ROWS = 50
MAX_LINES = 5
MAX_SLICES = 3  # the house style's limit; more is allowed with a tip
GRID_COLUMNS = 12  # the report canvas: visuals snap to a 12-column grid of rows
MAX_GRID_ROWS = 200
MAX_SHARE_ROWS = 12  # beyond this a pie or donut is unreadable, so the smallest slices are cut
ADDITIVE = ("sum", "count")


class ReportError(ValueError):
    pass


# State


def state(project: dict) -> dict:
    return project.setdefault("report", {"pages": [], "history": []})


def snapshot(project: dict) -> None:
    report = state(project)
    report["history"] = (report["history"] + [copy.deepcopy(report["pages"])])[-HISTORY_LIMIT:]


def undo(project: dict) -> None:
    report = state(project)
    if not report["history"]:
        raise ReportError("Nothing to undo.")
    report["pages"] = report["history"].pop()


def find_page(project: dict, page_id: str) -> dict:
    for page in state(project)["pages"]:
        if page["id"] == page_id:
            return page
    raise ReportError("Page not found.")


def find_visual(project: dict, visual_id: str) -> tuple[dict, dict]:
    for page in state(project)["pages"]:
        for visual in page["visuals"]:
            if visual["id"] == visual_id:
                return page, visual
    raise ReportError("Visual not found.")


# Checking visuals


def measure_named(project: dict, name) -> dict:
    for measure in model.state(project)["measures"]:
        if isinstance(name, str) and measure["name"].lower() == name.strip().lower():
            if measure.get("error"):
                raise ReportError(f"Measure '{measure['name']}' is broken: {measure['error']}")
            return measure
    raise ReportError(f"There is no measure called '{name}'.")


def column_ref(found, ref) -> dict:
    if not isinstance(ref, dict):
        raise ReportError("Columns are given as {table, column}.")
    table = model.find_table(found, ref.get("table"))
    column = table.column(ref.get("column"))
    return {"table": table.id, "column": column.name}


def check_visual(con, project: dict, page: dict, visual: dict) -> None:
    """Validate a visual in place (names normalised); raises ReportError or model.ModelError."""
    kind = visual.get("type")
    if kind not in VISUAL_TYPES:
        raise ReportError(f"Visual type must be one of: {', '.join(VISUAL_TYPES)}. Gauges and dual axes are not used.")
    if not isinstance(visual.get("title"), str) or not visual["title"].strip():
        raise ReportError("Every visual needs a plain-words title.")
    visual["title"] = visual["title"].strip()
    names = visual.get("measures")
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        raise ReportError("A visual needs at least one measure.")
    visual["measures"] = [measure_named(project, n)["name"] for n in names]
    found = model.tables(con, project)
    groups = visual.get("group_by") or []
    if isinstance(groups, dict):
        groups = [groups]
    visual["group_by"] = [column_ref(found, g) for g in groups]
    n_measures, n_groups = len(visual["measures"]), len(visual["group_by"])

    if kind == "kpi":
        if n_measures != 1 or n_groups:
            raise ReportError("A KPI card shows exactly one measure and no breakdown.")
        kpis = [v for v in page["visuals"] if v["type"] == "kpi" and v["id"] != visual["id"]]
        if len(kpis) >= MAX_KPIS:
            raise ReportError(f"A page holds at most {MAX_KPIS} KPI cards; put the rest on another page.")
        kpi = visual.setdefault("kpi", {})
        if kpi.get("direction", "higher_is_better") not in ("higher_is_better", "lower_is_better"):
            raise ReportError("KPI direction must be higher_is_better or lower_is_better.")
        kpi.setdefault("direction", "higher_is_better")
        if kpi.get("driver"):
            kpi["driver"] = column_ref(found, kpi["driver"])
    elif kind in ("bar", "column") + SHARE:
        if n_groups != 1:
            raise ReportError(f"A {kind} chart splits by exactly one column.")
        if kind in SHARE and n_measures != 1:
            raise ReportError(f"A {kind} chart shows one measure.")
        if kind not in SHARE and n_measures > 2:
            raise ReportError("Use at most two measures per bar or column chart.")
    elif kind in STACKED:
        if n_groups != 2 or n_measures != 1:
            raise ReportError("A stacked chart shows one measure, split by a category column and a column for the stacks.")
    elif kind in ("line", "area"):
        if n_groups != 1 or n_measures > MAX_LINES:
            raise ReportError(f"A {kind} chart has one x-axis column and at most {MAX_LINES} measures.")
    elif kind == "scatter":
        if n_groups != 1 or n_measures != 2:
            raise ReportError("A scatter plot shows two measures (across and up), one dot per value of one column.")
    elif kind == "table":
        if not 1 <= n_groups <= 3:
            raise ReportError("A table groups by one to three columns.")
    if len([v for v in page["visuals"] if v["id"] != visual["id"]]) >= MAX_VISUALS:
        raise ReportError(f"A page holds at most {MAX_VISUALS} visuals; one page should answer one question.")

    data = visual_data(con, project, visual)  # raises if the measures cannot be split this way
    visual["advice"] = advice(visual, data)


def advice(visual: dict, data: dict) -> list[str]:
    """House-style tips for a chart the style would not pick. Shown to creators; never blocks."""
    kind, slices = visual["type"], data.get("total_rows", 0)
    tips = []
    if kind in SHARE and slices > MAX_SLICES:
        tips.append(f"{slices} slices are hard to compare in a {kind}; a sorted bar chart shows the order at a glance.")
    if kind == "column" and slices > MAX_BARS:
        tips.append("Many columns crowd their labels; a horizontal bar chart reads more easily.")
    return tips


def grid_box(box) -> dict:
    values = [box.get(k) if isinstance(box, dict) else None for k in ("x", "y", "w", "h")]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        raise ReportError("A visual's place is given as whole grid cells {x, y, w, h}.")
    x, y, w, h = values
    if x < 0 or y < 0 or w < 1 or h < 2 or x + w > GRID_COLUMNS or y + h > MAX_GRID_ROWS:
        raise ReportError(f"A visual must fit inside the {GRID_COLUMNS}-column page.")
    return {"x": x, "y": y, "w": w, "h": h}


def set_layout(project: dict, page_id: str, positions: dict) -> None:
    """Move and resize visuals on a page's canvas."""
    page = find_page(project, page_id)
    visuals = {v["id"]: v for v in page["visuals"]}
    for visual_id, box in (positions or {}).items():
        if visual_id not in visuals:
            raise ReportError("Visual not found on this page.")
        visual = visuals[visual_id]
        visual["layout"] = grid_box(box)
        visual["width"] = "full" if visual["layout"]["w"] >= GRID_COLUMNS else "half"


def update_visual(con, project: dict, visual_id: str, changes: dict) -> dict:
    """Change a visual's type, title, fields or KPI settings; it is checked again like a new one."""
    page, visual = find_visual(project, visual_id)
    updated = copy.deepcopy(visual)
    for key in ("type", "title", "measures", "group_by", "kpi"):
        if changes.get(key) is not None:
            updated[key] = copy.deepcopy(changes[key])
    if updated["type"] != "kpi":
        updated["kpi"] = {}
    try:
        check_visual(con, project, page, updated)
    except model.ModelError as exc:
        raise ReportError(str(exc)) from exc
    if any(updated[k] != visual[k] for k in ("type", "measures", "group_by")):
        updated["insight"] = None  # written about the old numbers
    page["visuals"][page["visuals"].index(visual)] = updated
    return updated


def rename_page(project: dict, page_id: str, title: str) -> None:
    if not isinstance(title, str) or not title.strip():
        raise ReportError("A page needs a title.")
    find_page(project, page_id)["title"] = title.strip()


def fields(con, project: dict) -> list[dict]:
    """Tables, their visible columns and their measures, for building visuals by hand."""
    current = model.state(project)
    hidden = {(h["table"], h["column"]) for h in current["hidden"]}
    found = model.tables(con, project)
    out = []
    for table in found.values():
        out.append({
            "id": table.id,
            "name": table.name,
            "columns": [{"name": c.name, "type": c.type} for c in table.columns if (table.id, c.name) not in hidden],
            "measures": [m["name"] for m in current["measures"] if m.get("table") == table.id and not m.get("error")],
        })
    loose = [m["name"] for m in current["measures"] if m.get("table") not in found and not m.get("error")]
    if loose:
        out.insert(0, {"id": "", "name": "Measures", "columns": [], "measures": loose})
    return out


def add_page(project: dict, title: str, question: str = "", status: str = "Approved", reason: str = "") -> dict:
    if not isinstance(title, str) or not title.strip():
        raise ReportError("A page needs a title.")
    page = {"id": new_id(), "title": title.strip(), "question": str(question or ""), "visuals": [],
            "summary": "", "status": status, "reason": str(reason or ""), "added_at": now()}
    state(project)["pages"].append(page)
    return page


def add_visual(con, project: dict, page_id: str, proposal: dict, status: str) -> dict:
    page = find_page(project, page_id)
    visual = {
        "id": new_id(),
        "type": proposal.get("type"),
        "title": proposal.get("title"),
        "measures": copy.deepcopy(proposal.get("measures")),
        "group_by": copy.deepcopy(proposal.get("group_by") or []),
        "kpi": copy.deepcopy(proposal.get("kpi") or {}),
        "width": proposal.get("width") if proposal.get("width") in ("half", "full") else None,
        "insight": None,
        "reason": str(proposal.get("reason") or ""),
        "status": status,
        "added_at": now(),
    }
    try:
        check_visual(con, project, page, visual)
    except model.ModelError as exc:
        raise ReportError(str(exc)) from exc
    if visual["width"] is None:
        visual["width"] = "full" if visual["type"] == "table" else "half"
    if proposal.get("layout"):
        visual["layout"] = grid_box(proposal["layout"])
        visual["width"] = "full" if visual["layout"]["w"] >= GRID_COLUMNS else "half"
    page["visuals"].append(visual)
    order = {"kpi": 0, "table": 2}
    page["visuals"].sort(key=lambda v: order.get(v["type"], 1))  # KPIs on top, detail tables last
    return visual


# Computing data


def sort_key(value):
    return (value is None, value if isinstance(value, (int, float)) else str(value))


def visual_data(con, project: dict, visual: dict) -> dict:
    if visual["type"] == "kpi":
        return kpi_data(con, project, visual)
    result = model.evaluate(con, project, visual["measures"], visual["group_by"], limit=model.MAX_GROUPS)
    rows = result["rows"]
    groups = len(visual["group_by"])
    kind = visual["type"]
    if kind in ("bar", "table", "scatter") + SHARE:
        # Largest first; ordered categories (columns, lines) keep their natural order.
        rows = sorted(rows, key=lambda r: (r[groups] is None, -(r[groups] or 0)))
    limit = {"bar": MAX_BARS, "table": MAX_TABLE_ROWS, "pie": MAX_SHARE_ROWS, "donut": MAX_SHARE_ROWS}.get(kind)
    shown = rows[:limit] if limit else rows
    data = {
        "columns": result["columns"],
        "rows": shown,
        "total_rows": len(rows),
        "truncated": len(shown) < len(rows),
    }
    data["check"] = reconcile(con, project, visual, rows)
    return data


def reconcile(con, project: dict, visual: dict, rows: list) -> dict | None:
    """For a sum or count split by one column, the parts must add up to the total."""
    if len(visual["group_by"]) != 1:
        return None
    measure = measure_named(project, visual["measures"][0])
    if measure["kind"] != "aggregate" or measure["aggregation"] not in ADDITIVE:
        return None
    total = model.evaluate(con, project, [measure["name"]])["rows"][0][0] or 0
    parts = sum(r[1] or 0 for r in rows)
    ok = math.isclose(parts, total, rel_tol=1e-9, abs_tol=1e-6)
    return {"total": total, "parts": parts, "ok": ok}


def date_filters(start: date, end: date) -> list[dict]:
    return [
        {"table": model.DATE_ID, "column": "Date", "operator": "at_least", "value": start.isoformat()},
        {"table": model.DATE_ID, "column": "Date", "operator": "at_most", "value": end.isoformat()},
    ]


def same_day_last_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 1, day=28)


def last_data_date(con, project: dict, measure: str) -> date | None:
    months = model.evaluate(con, project, [measure], [{"table": model.DATE_ID, "column": "Year Month"}], limit=model.MAX_GROUPS)
    filled = [r[0] for r in months["rows"] if r[0] is not None and r[1] not in (None, 0)]
    if not filled:
        return None
    month = max(filled)
    first = date.fromisoformat(f"{month}-01")
    days = model.evaluate(con, project, [measure], [{"table": model.DATE_ID, "column": "Date"}],
                          filters=date_filters(first, first + timedelta(days=31)), limit=model.MAX_GROUPS)
    return max(date.fromisoformat(r[0]) for r in days["rows"] if r[0] is not None and r[1] not in (None, 0))


def kpi_data(con, project: dict, visual: dict) -> dict:
    name = visual["measures"][0]
    total = model.evaluate(con, project, [name])["rows"][0][0]
    card = {"value": total, "period": "All data", "previous": None, "delta": None, "delta_pct": None,
            "comparison": None, "sparkline": [], "driver": None, "note": None}
    if model.DATE_ID not in model.tables(con, project):
        card["note"] = "No comparison: the model has no date table."
        return card
    try:
        last = last_data_date(con, project, name)
    except model.ModelError:
        card["note"] = "No comparison: this measure is not related to the date table."
        return card
    if last is None:
        card["note"] = "No comparison: no dated values."
        return card

    start = date(last.year, 1, 1)
    py_start, py_end = date(last.year - 1, 1, 1), same_day_last_year(last)
    value = model.evaluate(con, project, [name], filters=date_filters(start, last))["rows"][0][0]
    previous = model.evaluate(con, project, [name], filters=date_filters(py_start, py_end))["rows"][0][0]
    card.update(value=value, period=f"{last.year} to date (to {last.isoformat()})", previous=previous,
                comparison=f"vs same period {last.year - 1}")
    if previous is None or value is None:
        card["note"] = f"No comparison: no data for the same period in {last.year - 1}."
    else:
        card["delta"] = value - previous
        card["delta_pct"] = (value - previous) / abs(previous) if previous else None

    spark_start = date(last.year - (1 if last.month < 12 else 0), (last.month % 12) + 1, 1)
    months = model.evaluate(con, project, [name], [{"table": model.DATE_ID, "column": "Year Month"}],
                            filters=date_filters(spark_start, last))
    card["sparkline"] = [{"label": r[0], "value": r[1]} for r in months["rows"] if r[0] is not None and r[1] is not None]

    driver = visual.get("kpi", {}).get("driver")
    measure = measure_named(project, name)
    if driver and card["delta"] and measure["kind"] == "aggregate" and measure["aggregation"] in ADDITIVE:
        group = [driver]
        now_by = dict(map(tuple, model.evaluate(con, project, [name], group, date_filters(start, last))["rows"]))
        then_by = dict(map(tuple, model.evaluate(con, project, [name], group, date_filters(py_start, py_end))["rows"]))
        changes = [(k, (now_by.get(k) or 0) - (then_by.get(k) or 0)) for k in set(now_by) | set(then_by)]
        same_way = [c for c in changes if c[1] and (c[1] > 0) == (card["delta"] > 0)]
        if same_way:
            top = max(same_way, key=lambda c: (abs(c[1]), str(c[0])))
            card["driver"] = {"name": "(blank)" if top[0] is None else str(top[0]), "delta": top[1],
                              "column": f"{model.find_table(model.tables(con, project), driver['table']).name}[{driver['column']}]"}
    return card


# Checking the numbers in AI insights

NUMBER = re.compile(r"(?<![\w.])[-+]?(?:RM\s?)?\d[\d,]*(?:\.\d+)?\s?(?:%|[KkMm]\b|bn\b)?")


def numbers_in(text: str) -> list[tuple[float, bool, float]]:
    """Numbers quoted in a sentence, as (value, is_percent, rounding tolerance).

    A number is as precise as it is written: "6%" covers 5.5% to 6.5%, "RM 1.2M" covers
    1.15M to 1.25M. Years and small counts like "top 3" are skipped.
    """
    found = []
    for raw in NUMBER.findall(text or ""):
        token = raw.replace("RM", "").replace(",", "").replace(" ", "")
        percent = token.endswith("%")
        token = token.rstrip("%")
        scale = 1
        if token[-1:] in "Kk":
            scale, token = 1e3, token[:-1]
        elif token[-1:] in "Mm":
            scale, token = 1e6, token[:-1]
        elif token.endswith("bn"):
            scale, token = 1e9, token[:-2]
        try:
            value = float(token) * scale
        except ValueError:
            continue
        if not percent and scale == 1 and value.is_integer() and (1900 <= value <= 2100 or abs(value) <= 12):
            continue
        decimals = len(token.split(".")[1]) if "." in token else 0
        found.append((value, percent, 0.5 * 10 ** -decimals * scale))
    return found


def check_insight(text: str, data: dict) -> dict:
    """Which numbers in an insight match the visual's data (values, changes, shares, ratios)."""
    values, percents = [], []
    if "value" in data:  # KPI card
        values += [v for v in (data["value"], data["previous"], data["delta"]) if isinstance(v, (int, float))]
        if data.get("delta_pct") is not None:
            percents.append(data["delta_pct"] * 100)
        if data.get("driver"):
            values.append(data["driver"]["delta"])
        values += [p["value"] for p in data.get("sparkline", [])]
    else:
        numeric = [[v for v in row if isinstance(v, (int, float)) and not isinstance(v, bool)] for row in data["rows"]]
        values += [v for row in numeric for v in row]
        column_totals = {}
        for row in data["rows"]:
            for i, v in enumerate(row):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    column_totals[i] = column_totals.get(i, 0) + v
        values += list(column_totals.values())
        for row in data["rows"]:
            for i, v in enumerate(row):
                if isinstance(v, (int, float)) and column_totals.get(i):
                    percents.append(v / column_totals[i] * 100)
        # "The top five together make RM 71,500 (47%)": running totals of the first rows (shown largest first).
        columns = {}
        for row in data["rows"]:
            for i, v in enumerate(row):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    columns.setdefault(i, []).append(v)
        for i, column in columns.items():
            running = 0
            for v in column[:10]:
                running += v
                values.append(running)
                if column_totals.get(i):
                    percents.append(running / column_totals[i] * 100)
        flat = [v for row in numeric for v in row]
        for a in flat:
            for b in flat:
                if b:
                    percents.append((a - b) / abs(b) * 100)
                    values.append(a - b)
                    values.append(a / b)
        if data.get("check"):
            values.append(data["check"]["total"])
    values = [abs(v) for v in values]
    percents = [abs(p) for p in percents] + [v * 100 for v in values if v <= 1]

    def matches(number, tolerance, pool):
        return any(abs(number - p) <= tolerance + 1e-9 or math.isclose(number, p, rel_tol=0.005) for p in pool)

    unmatched = []
    for value, percent, tolerance in numbers_in(text):
        pool = percents if percent else values + percents
        if not matches(abs(value), tolerance, pool):
            unmatched.append(value)
    return {"verified": not unmatched, "unmatched": unmatched}


# Data as of


def as_of(con, project: dict) -> str | None:
    found = model.tables(con, project)
    if model.DATE_ID not in found:
        return None
    latest = None
    for rel in model.state(project)["relationships"]:
        if rel["to_table"] == model.DATE_ID and rel.get("active") and not rel.get("error") and rel["from_table"] in found:
            table = found[rel["from_table"]]
            value = con.execute(f"SELECT max({model.quote(rel['from_column'])}) FROM {table.sql}").fetchone()[0]
            if value is not None and (latest is None or value > latest):
                latest = value
    return latest.isoformat() if latest else None
