"""The Report agent: designs pages of visuals, then writes insights from their data.

Pass 1 sees the model (measures with definitions and values, dimension columns with a
few values, the date range) and proposes pages. Each visual goes through the same checks
as a manual one. Pass 2 sees each kept visual's computed data (aggregates only) and
writes one insight sentence per visual plus a page summary. Numbers in insights are
then checked against that data.
"""

import json

from . import llm, model, report

DESIGN_SYSTEM = (
    "You are the Report agent in an AI data analytics app, like Power BI's Report view, designing for "
    "non-technical business users. Rules: one page answers one question and its title says so; at most "
    "7 KPI cards per page (aim for 3 to 5), the most important first; every chart title describes the "
    "content in plain words (no conclusions in titles); compare categories with a horizontal 'bar' (it is "
    "sorted by value automatically); show trends with 'line' over a date column such as Date[Year Month]; "
    "use 'column' only for ordered categories like Year or Quarter; 'stacked_bar' or 'stacked_column' "
    "(group_by: category column, then the stacks column) to show parts of each category; 'area' for a "
    "cumulative trend; 'scatter' (two measures, one dot per value of a column) to show a relationship; "
    "'pie' or 'donut' only for 3 or fewer slices when you choose; "
    "a 'table' for detail, last on the page. No gauges, no dual axes. For each KPI set direction "
    "(lower_is_better for costs, delays, defects) and, when it helps explain change, a driver column from a "
    "dimension table. Prefer 1 to 3 pages. Give a short reason for each visual."
)

INSIGHT_SYSTEM = (
    "You write insights for business users. For each visual, write one short sentence (max 25 words) that "
    "says what matters in its data: the biggest item, the change, or the surprise. Only use numbers that "
    "appear in or follow directly from the data given (values, differences, shares of total). Round sensibly "
    "(RM 1.2M, 34%). Then write a two-sentence summary per page. Plain words, no jargon, no speculation "
    "about causes the data does not show."
)

VISUAL = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(report.VISUAL_TYPES)},
        "title": {"type": "string"},
        "measures": {"type": "array", "items": {"type": "string"}},
        "group_by": {
            "type": "array",
            "items": {"type": "object", "properties": {"table": {"type": "string"}, "column": {"type": "string"}}},
        },
        "kpi": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["higher_is_better", "lower_is_better"]},
                "driver": {"type": "object", "properties": {"table": {"type": "string"}, "column": {"type": "string"}}},
            },
        },
        "width": {"type": "string", "enum": ["half", "full"]},
        "reason": {"type": "string"},
    },
    "required": ["type", "title", "measures"],
}

DESIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "template": {"type": "string", "description": "Only when asked to pick a template: its id."},
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "question": {"type": "string"},
                    "visuals": {"type": "array", "items": VISUAL},
                },
                "required": ["title", "visuals"],
            },
        },
    },
    "required": ["summary", "pages"],
}

INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "visuals": {
            "type": "array",
            "items": {"type": "object", "properties": {"id": {"type": "string"}, "insight": {"type": "string"}}},
        },
        "pages": {
            "type": "array",
            "items": {"type": "object", "properties": {"id": {"type": "string"}, "summary": {"type": "string"}}},
        },
    },
    "required": ["visuals"],
}


def design_prompt(con, project: dict, brief: str = "") -> str:
    found = model.tables(con, project)
    current = model.state(project)
    names = {t.id: t.name for t in found.values()}
    measures = []
    for m in current["measures"]:
        if m.get("error"):
            continue
        try:
            value = model.evaluate(con, project, [m["name"]])["rows"][0][0]
        except model.ModelError:
            value = None
        measures.append({"name": m["name"], "definition": model.describe_measure(m, names),
                         "format": m["format"]["type"], "value": value, "description": m.get("description")})
    hidden = {(h["table"], h["column"]) for h in current["hidden"]}
    columns = []
    for table in found.values():
        for column in table.columns:
            if (table.id, column.name) in hidden:
                continue
            distinct, sample = con.execute(
                f"SELECT count(DISTINCT {model.quote(column.name)}), "
                f"(SELECT list(v) FROM (SELECT DISTINCT {model.quote(column.name)} AS v FROM {table.sql} "
                f"WHERE {model.quote(column.name)} IS NOT NULL ORDER BY 1 LIMIT 4)) FROM {table.sql}"
            ).fetchone()
            columns.append({"table": table.name, "column": column.name, "type": column.type,
                            "distinct": distinct, "examples": [str(s) for s in sample or []]})
    relationships = [
        f"{names.get(r['from_table'])}[{r['from_column']}] -> {names.get(r['to_table'])}[{r['to_column']}]"
        for r in current["relationships"] if r.get("active") and not r.get("error")
    ]
    return (
        f"Measures:\n{json.dumps(measures, default=str)}\n\n"
        f"Columns you can split by (only via active relationships from the measure's table):\n"
        f"{json.dumps(columns, default=str)}\n\nActive relationships:\n{json.dumps(relationships)}\n\n"
        f"Data as of: {report.as_of(con, project)}\n\n" + (f"{brief}\n\n" if brief else "") + "Design the report pages."
    )


def design(client, model_name: str, capabilities: dict, con, project: dict, brief: str = "") -> dict:
    """Pages for this model; `brief` (a template, the user's description) says what to aim for (see templates.py)."""
    return llm.ask_json(client, model_name, capabilities, DESIGN_SYSTEM, design_prompt(con, project, brief),
                        "design_report", DESIGN_SCHEMA)


def apply_design(con, project: dict, proposal: dict) -> dict:
    """Add the proposed pages, keeping each visual that passes the checks."""
    added, refused, pages = [], [], []
    for spec in proposal.get("pages") or []:
        if not isinstance(spec, dict):
            continue
        try:
            page = report.add_page(project, spec.get("title"), spec.get("question", ""), "AI")
        except report.ReportError as exc:
            refused.append({"kind": "page", "label": str(spec.get("title")), "error": str(exc)})
            continue
        for item in spec.get("visuals") or []:
            if not isinstance(item, dict):
                continue
            try:
                report.add_visual(con, project, page["id"], item, "AI")
                added.append({"kind": item.get("type"), "label": str(item.get("title"))})
            except (report.ReportError, model.ModelError) as exc:
                refused.append({"kind": str(item.get("type")), "label": str(item.get("title")), "error": str(exc)})
        if page["visuals"]:
            pages.append(page)
        else:
            report.state(project)["pages"].remove(page)
    return {"summary": str(proposal.get("summary") or ""), "added": added, "refused": refused, "pages": pages,
            "template": proposal.get("template") if isinstance(proposal.get("template"), str) else None}


def insight_prompt(pages: list[dict], data: dict[str, dict]) -> str:
    payload = []
    for page in pages:
        payload.append({
            "id": page["id"],
            "title": page["title"],
            "question": page["question"],
            "visuals": [
                {"id": v["id"], "type": v["type"], "title": v["title"], "measures": v["measures"],
                 "data": data.get(v["id"])}
                for v in page["visuals"]
            ],
        })
    return f"Report pages with each visual's data:\n{json.dumps(payload, default=str)}\n\nWrite the insights."


def write_insights(client, model_name: str, capabilities: dict, con, project: dict, pages: list[dict]) -> int:
    """Ask for insights on the given pages, store them with their number check. Returns how many were written."""
    data = {}
    for page in pages:
        for visual in page["visuals"]:
            data[visual["id"]] = report.visual_data(con, project, visual)
    if not data:
        return 0
    raw = llm.ask_json(client, model_name, capabilities, INSIGHT_SYSTEM, insight_prompt(pages, data), "write_insights",
                       INSIGHT_SCHEMA)
    by_id = {v["id"]: v for p in pages for v in p["visuals"]}
    written = 0
    for item in raw.get("visuals") or []:
        if isinstance(item, dict) and item.get("id") in by_id and isinstance(item.get("insight"), str):
            text = item["insight"].strip()[:300]
            by_id[item["id"]]["insight"] = {"text": text, "check": report.check_insight(text, data[item["id"]])}
            written += 1
    page_ids = {p["id"]: p for p in pages}
    for item in raw.get("pages") or []:
        if isinstance(item, dict) and item.get("id") in page_ids and isinstance(item.get("summary"), str):
            page_ids[item["id"]]["summary"] = item["summary"].strip()[:600]
    return written
