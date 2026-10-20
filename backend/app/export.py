"""The Export agent: an executive summary for a dashboard's PDF.

The AI sees each published page's title, summary and its visuals' computed data, and writes
3 to 5 short bullets. Every number in a bullet must match the data of at least one visual
(values, changes, shares, ratios, at the precision written); bullets with numbers that match
nothing are flagged, as insights are.
"""

import json

from . import llm, report

SYSTEM = (
    "You write the executive summary for a dashboard exported to PDF, for busy managers. Write 3 to 5 bullets, "
    "most important first, each one short sentence. Use only numbers that appear in or follow directly from the "
    "data given (values, differences, shares). Round sensibly (RM 1.2M, 34%). No speculation about causes the data "
    "does not show. Plain words, no jargon."
)

SCHEMA = {
    "type": "object",
    "properties": {"bullets": {"type": "array", "items": {"type": "string"}}},
    "required": ["bullets"],
}


def compact(data: dict) -> dict:
    """Visual data trimmed for the prompt."""
    if "value" in data:
        return {k: data.get(k) for k in ("value", "previous", "delta", "delta_pct", "period", "comparison", "driver")}
    return {"columns": data["columns"], "rows": data["rows"][:15]}


def unmatched_everywhere(text: str, datasets: list[dict]) -> list[float]:
    """Numbers in `text` that match none of the visuals' data."""
    if not datasets:
        return [value for value, _, _ in report.numbers_in(text)]
    misses = [set(report.check_insight(text, data)["unmatched"]) for data in datasets]
    return sorted(set.intersection(*misses))


def summarise(client, model_name: str, capabilities: dict, pages: list[dict], data: dict[str, dict]) -> list[dict]:
    payload = [
        {
            "page": page["title"],
            "summary": page.get("summary"),
            "visuals": [{"title": v["title"], "type": v["type"], "data": compact(data[v["id"]])}
                        for v in page["visuals"] if v["id"] in data],
        }
        for page in pages
    ]
    prompt = f"Dashboard pages and their data:\n{json.dumps(payload, default=str)}\n\nWrite the executive summary."
    raw = llm.ask_json(client, model_name, capabilities, SYSTEM, prompt, "executive_summary", SCHEMA)
    bullets = [str(b).strip()[:300] for b in raw.get("bullets") or [] if str(b).strip()][:5]
    values = list(data.values())
    return [{"text": text, "unmatched": unmatched_everywhere(text, values)} for text in bullets]
