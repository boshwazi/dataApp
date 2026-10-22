"""Dashboard templates: page blueprints the Report AI fills with a dataset's own measures.

A template is a few pages, each a question with slots such as "the main money measure by month".
Slots name kinds of measures and columns, not particular ones: the Report AI picks the closest
measure and column in the model for each slot, and skips a slot nothing fits (it never invents
measures). Every visual still goes through the usual checks.

A dashboard can start three ways: from a template, from a description in the user's words (with
or without a template), or by letting the AI pick the template that fits the data best.
"""

from fastapi import APIRouter

TEMPLATES = [
    {
        "id": "executive",
        "name": "Executive overview",
        "description": "The few numbers leaders check first, how they are trending, and where they come from.",
        "pages": [
            {"title": "How is the business doing?", "slots": [
                "3 to 5 KPI cards: the main money measure first (revenue or sales), then volume (orders, units or "
                "transactions), then an average or rate; each with its year-to-date comparison when there is a date table",
                "the main money measure by month (line over Date[Year Month])",
                "the main money measure by the most important category, such as product line or segment (bar)",
            ]},
            {"title": "Where does it come from?", "slots": [
                "the main money measure by region, branch or location (bar)",
                "the main money measure by product or category (bar)",
                "a detail table of the top items with the main money and volume measures",
            ]},
        ],
    },
    {
        "id": "sales",
        "name": "Sales performance",
        "description": "Revenue, orders and average order value over time, by product, channel and salesperson.",
        "pages": [
            {"title": "Are sales growing?", "slots": [
                "KPI cards: revenue, orders, average order value, and revenue year to date against last year if those "
                "measures exist",
                "revenue by month (line over Date[Year Month])",
                "orders by quarter or year (column)",
            ]},
            {"title": "What sells, and through which channels?", "slots": [
                "revenue by product or product category (bar)",
                "revenue by channel, store or region, split by category (stacked_bar) when both columns exist",
                "average order value by segment or channel (bar)",
            ]},
            {"title": "Who sells and who buys the most?", "slots": [
                "revenue by salesperson, agent or team (bar)",
                "a table of the top customers with revenue and orders",
            ]},
        ],
    },
    {
        "id": "operations",
        "name": "Operations",
        "description": "Volumes, turnaround times and problems such as delays, defects or returns, by site and team.",
        "pages": [
            {"title": "How much are we handling, and how fast?", "slots": [
                "KPI cards: volume (jobs, tickets, shipments or units), average time or duration (lower_is_better), and a "
                "problem rate such as delays, defects or returns (lower_is_better)",
                "volume by month (line over Date[Year Month])",
                "average time or duration by month (line)",
            ]},
            {"title": "Where are the bottlenecks?", "slots": [
                "the problem measure by site, team or stage (bar)",
                "average time by category or type (bar)",
                "a detail table of the slowest or most problematic items",
            ]},
        ],
    },
    {
        "id": "finance",
        "name": "Finance",
        "description": "Revenue, cost, profit and margin over time, with where the money goes.",
        "pages": [
            {"title": "Are we making money?", "slots": [
                "KPI cards: revenue, cost (lower_is_better), profit and margin %; budget against actual when budget "
                "figures exist",
                "revenue and cost by month (line with both measures)",
                "profit by quarter (column)",
            ]},
            {"title": "Where does the money go?", "slots": [
                "cost by cost category or account (bar)",
                "profit or margin by business unit, product or region (bar)",
                "a table by account or category with revenue, cost and profit",
            ]},
        ],
    },
    {
        "id": "customers",
        "name": "Customers",
        "description": "How many customers buy, how much each is worth, and who they are.",
        "pages": [
            {"title": "How many customers buy, and how much?", "slots": [
                "KPI cards: active customers (a distinct count), revenue per customer, orders per customer",
                "active customers by month (line over Date[Year Month])",
                "revenue by customer segment (bar)",
            ]},
            {"title": "Who are our best customers?", "slots": [
                "revenue by customer location, state or region (bar)",
                "a table of the top customers with revenue, orders and their segment",
            ]},
        ],
    },
]
BY_ID = {t["id"]: t for t in TEMPLATES}


class TemplateError(ValueError):
    pass


def blueprint(template: dict) -> str:
    lines = [f"Template: {template['name']} ({template['description']})"]
    for n, page in enumerate(template["pages"], 1):
        lines.append(f"Page {n}: \"{page['title']}\"")
        lines += [f"  - {slot}" for slot in page["slots"]]
    return "\n".join(lines)


def brief(template_id: str | None = None, description: str | None = None, choose: bool = False) -> str:
    """What the Report AI is asked to follow, on top of its usual rules. Empty: design freely."""
    parts = []
    description = (description or "").strip()[:1000]
    if template_id:
        if template_id not in BY_ID:
            raise TemplateError(f"There is no template called '{template_id}'.")
        parts.append("Follow this dashboard template. Use these page titles (adjust the words to the data if needed). "
                     "Fill each slot with the closest measure and column in this model; skip a slot when nothing "
                     "fits, and never invent measures or columns.\n" + blueprint(BY_ID[template_id]))
    elif choose:
        parts.append("First pick the template below that fits this data best, set 'template' to its id, and follow "
                     "it: use its page titles and fill each slot with the closest measure and column in this model, "
                     "skipping slots nothing fits.\n\n" + "\n\n".join(f"id {t['id']}: " + blueprint(t) for t in TEMPLATES))
    if description:
        parts.append(f"The user describes the dashboard they want, in their words: \"{description}\". "
                     "Design the pages to answer that" + (", within the template." if parts else "."))
    return "\n\n".join(parts)


def summary(template: dict) -> dict:
    return {"id": template["id"], "name": template["name"], "description": template["description"],
            "pages": [p["title"] for p in template["pages"]]}


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/dashboard-templates")

    @router.get("")
    def list_templates():
        return [summary(t) for t in TEMPLATES]

    return router
