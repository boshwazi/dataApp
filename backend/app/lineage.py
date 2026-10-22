"""Lineage: where every number on a dashboard comes from.

The graph runs left to right: sources (files, sheets, linked tables) → Transform tables
(including lookups, merges and appends) → measures (and the measures formulas use) →
dashboard visuals (by their measures, and by the tables they are split by).
"""

from . import model, model_expr, transform


def origin_text(source: dict) -> str:
    origin = source["origin"]
    if origin.get("kind") == "gsheet":
        return "Google Sheet"
    if origin.get("kind") == "dataset":
        return f"linked from {origin.get('dataset_name')} v{origin.get('version')}"
    return origin.get("file") or origin.get("kind", "")


def build(project: dict, dashboards: list[dict]) -> dict:
    nodes, edges = [], []

    def node(node_id, kind, label, sub, column):
        nodes.append({"id": node_id, "kind": kind, "label": label, "sub": sub, "column": column})

    def edge(a, b, kind="uses"):
        edges.append({"from": a, "to": b, "kind": kind})

    for source in project["sources"]:
        node(f"source:{source['id']}", "source", source["table"],
             f"{origin_text(source)} · {source['profile']['rows']:,} rows", 0)

    queries = transform.state(project)["queries"]
    for q in queries:
        sub = f"{len(q['steps'])} {'step' if len(q['steps']) == 1 else 'steps'}"
        if q.get("base_query"):
            sub = "lookup table · " + sub
        if q.get("error"):
            sub += " · error"
        node(f"query:{q['id']}", "table", q["name"], sub, 1)
        if q.get("base_query"):
            edge(f"query:{q['base_query']}", f"query:{q['id']}", "split")
        elif q.get("source_id"):
            edge(f"source:{q['source_id']}", f"query:{q['id']}")
        for step in q["steps"]:
            params = step.get("params") or {}
            if step["op"] == "merge" and isinstance(params.get("query"), str):
                edge(f"query:{params['query']}", f"query:{q['id']}", "merge")
            if step["op"] == "append":
                for other in params.get("queries") or []:
                    edge(f"query:{other}", f"query:{q['id']}", "append")

    current = model.state(project)
    if current.get("date_table"):
        node(f"query:{model.DATE_ID}", "table", model.DATE_NAME, "generated date table", 1)

    by_name = {m["name"].lower(): m for m in current["measures"]}
    names = transform.query_names(project) | {model.DATE_ID: model.DATE_NAME}
    for m in current["measures"]:
        node(f"measure:{m['name']}", "measure", m["name"], model.describe_measure(m, names), 2)
        if m["kind"] == "aggregate" and m.get("table"):
            edge(f"query:{m['table']}", f"measure:{m['name']}")
        elif m["kind"] in ("formula", "time"):
            try:
                refs = model.measure_refs(m)
            except model_expr.ExprError:
                refs = []
            for ref in refs:
                if ref.lower() in by_name:
                    edge(f"measure:{by_name[ref.lower()]['name']}", f"measure:{m['name']}")

    table_ids = {q["name"].lower(): q["id"] for q in queries} | {model.DATE_NAME.lower(): model.DATE_ID}
    for dash in dashboards:
        published = (dash.get("published") or {}).get("pages")
        pages = published if published else dash["draft"]["pages"]
        state = "published" if published else "draft"
        for page in pages:
            for visual in page["visuals"]:
                vid = f"visual:{dash['id']}:{visual['id']}"
                node(vid, "visual", visual["title"], f"{dash['name']} › {page['title']} ({state})", 3)
                for name in visual["measures"]:
                    if name.lower() in by_name:
                        edge(f"measure:{by_name[name.lower()]['name']}", vid)
                for group in visual.get("group_by") or []:
                    ref = str(group.get("table"))
                    target = ref if any(q["id"] == ref for q in queries) or ref == model.DATE_ID else table_ids.get(ref.lower())
                    if target:
                        edge(f"query:{target}", vid, "split")

    known = {n["id"] for n in nodes}
    edges = [e for e in edges if e["from"] in known and e["to"] in known]
    unique = list({(e["from"], e["to"], e["kind"]): e for e in edges}.values())
    return {"nodes": nodes, "edges": unique}
