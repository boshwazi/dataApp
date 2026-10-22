"""A dashboard's pages as Power BI report pages (PBIR), for the .pbip export.

Each datawazi visual becomes the matching Power BI visual, bound to the exported model's measures and
columns, in the same place on the page (the 12-column grid scaled to Power BI's 1280-pixel canvas):

  kpi → cardVisual          bar → clusteredBarChart (sorted by value)   column → clusteredColumnChart
  stacked_bar → barChart     stacked_column → columnChart (the second column is the legend)
  line → lineChart           area → areaChart           pie → pieChart         donut → donutChart
  scatter → scatterChart     table → tableEx

Titles carry over. Page filters, slicers and AI insights don't yet: the README lists what was left out.
"""

import json
import uuid

from . import model

WIDTH = 1280
MARGIN = 16
ROW = 40  # pixels per grid row
CELL = (WIDTH - 2 * MARGIN) / 12
VISUAL_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.9.0/schema.json"
PAGE_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"
TYPES = {"kpi": "cardVisual", "bar": "clusteredBarChart", "column": "clusteredColumnChart", "stacked_bar": "barChart",
         "stacked_column": "columnChart", "line": "lineChart", "area": "areaChart", "pie": "pieChart",
         "donut": "donutChart", "scatter": "scatterChart", "table": "tableEx"}
DEFAULT_SIZE = {"kpi": (3, 3)}  # grid cells for visuals never placed by hand; others: half or full width


def name() -> str:
    return uuid.uuid4().hex[:20]


def literal(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def measure_field(table: str, measure: str) -> dict:
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": measure}},
            "queryRef": f"{table}.{measure}", "nativeQueryRef": measure}


def column_field(table: str, column: str) -> dict:
    return {"field": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}},
            "queryRef": f"{table}.{column}", "nativeQueryRef": column}


def places(visuals: list[dict]) -> dict[str, dict]:
    """Each visual's grid box: its own, or the next free spot in reading order, as the canvas shows it."""
    boxes, row, x, row_height = {}, 0, 0, 0
    placed = [v for v in visuals if v.get("layout")]
    if placed:
        row = max(v["layout"]["y"] + v["layout"]["h"] for v in placed)
    for visual in visuals:
        if visual.get("layout"):
            boxes[visual["id"]] = visual["layout"]
            continue
        w, h = DEFAULT_SIZE.get(visual["type"], (12 if visual.get("width") == "full" else 6, 8))
        if x + w > 12:
            row, x, row_height = row + row_height, 0, 0
        boxes[visual["id"]] = {"x": x, "y": row, "w": w, "h": h}
        x, row_height = x + w, max(row_height, h)
    return boxes


def visual_json(visual: dict, box: dict, z: int, measure_table: dict[str, str], table_names: dict[str, str]) -> dict | None:
    kind = TYPES.get(visual["type"])
    if kind is None:
        return None
    measures = [measure_field(measure_table[m], m) for m in visual["measures"] if m in measure_table]
    if not measures:
        return None
    groups = [column_field(table_names.get(g["table"], g["table"]), g["column"])
              for g in visual.get("group_by") or [] if g.get("table") in table_names or g.get("table") == model.DATE_ID]
    if visual["type"] == "kpi":
        state = {"Data": {"projections": measures[:1]}}
    elif visual["type"] == "table":
        state = {"Values": {"projections": groups + measures}}
    elif visual["type"] == "scatter":
        state = {"Category": {"projections": groups[:1]}, "X": {"projections": measures[:1]},
                 "Y": {"projections": measures[1:2] or measures[:1]}}
    elif visual["type"] in ("stacked_bar", "stacked_column"):
        state = {"Category": {"projections": groups[:1]}, "Y": {"projections": measures[:1]}}
        if len(groups) > 1:
            state["Series"] = {"projections": groups[1:2]}
    else:
        state = {"Category": {"projections": groups[:1]}, "Y": {"projections": measures}}
    query = {"queryState": state}
    if visual["type"] in ("bar", "column", "pie", "donut") and groups:
        # Sorted by the first measure, largest first, as datawazi shows it.
        query["sortDefinition"] = {"sort": [{"field": measures[0]["field"], "direction": "Descending"}], "isDefaultSort": True}
    vid = name()
    return {
        "$schema": VISUAL_SCHEMA,
        "name": vid,
        "position": {"x": round(MARGIN + box["x"] * CELL, 1), "y": round(MARGIN + box["y"] * ROW, 1), "z": z,
                     "width": round(box["w"] * CELL - 8, 1), "height": round(box["h"] * ROW - 8, 1), "tabOrder": z},
        "visual": {
            "visualType": kind,
            "query": query,
            "visualContainerObjects": {
                "title": [{"properties": {"show": literal("true"), "text": literal(f"'{visual['title'].replace(chr(39), chr(39) * 2)}'")}}],
            },
            "drillFilterOtherVisuals": True,
        },
    }


def page_files(base: str, pages: list[dict], measure_table: dict[str, str], table_names: dict[str, str]) -> tuple[dict, list[str], list[str]]:
    """The report's page folders (path → JSON), the page order, and notes on what didn't carry over."""
    files, order, notes = {}, [], []
    for page in pages:
        pid = name()
        order.append(pid)
        boxes = places(page["visuals"])
        bottom = max([b["y"] + b["h"] for b in boxes.values()] or [0])
        files[f"{base}/pages/{pid}/page.json"] = {
            "$schema": PAGE_SCHEMA, "name": pid, "displayName": page["title"][:100],
            "displayOption": "FitToWidth", "width": WIDTH, "height": max(720, int(2 * MARGIN + bottom * ROW))}
        for z, visual in enumerate(page["visuals"], start=1):
            made = visual_json(visual, boxes[visual["id"]], z * 1000, measure_table, table_names)
            if made is None:
                notes.append(f"{page['title']}: {visual['title']} couldn't be drawn in Power BI (its measures weren't exported).")
                continue
            files[f"{base}/pages/{pid}/visuals/{made['name']}/visual.json"] = made
        if page.get("filters") or page.get("slicers"):
            notes.append(f"{page['title']}: page filters and slicers aren't carried over yet; add them in Power BI.")
    return {path: (json.dumps(value, indent=2) + "\n").encode() for path, value in files.items()}, order, notes
