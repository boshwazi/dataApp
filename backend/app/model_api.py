"""HTTP routes for the Model stage."""

from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import guidance, activity, ai_model, llm, model, transform
from .projects import NotFound, ProjectStore
from .settings import ProviderSettings, SettingsStore


class RelationshipBody(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    reason: str = ""


class MeasureBody(BaseModel):
    name: str
    kind: str
    table: str | None = None
    aggregation: str | None = None
    expression: str | None = None
    filters: list[dict] = []
    base: str | None = None  # time measures: the measure they work out over time
    calc: str | None = None
    months: int | None = None
    use_relationship: str | None = None  # an inactive relationship to use instead, like USERELATIONSHIP
    format: dict = {"type": "number", "decimals": 0}
    description: str = ""
    folder: str = ""
    reason: str = ""


class MeasureTrialBody(MeasureBody):
    id: str | None = None


class LayoutBody(BaseModel):
    positions: dict[str, dict]


class DateTableBody(BaseModel):
    start_year: int | None = None
    end_year: int | None = None


class HiddenBody(BaseModel):
    table: str
    column: str
    hidden: bool = True


class EvaluateBody(BaseModel):
    measures: list[str]
    group_by: list[dict] = []
    filters: list[dict] = []
    limit: int = model.MAX_GROUPS


def view(con, project: dict) -> dict:
    current = model.state(project)
    found = model.tables(con, project)
    names = {t.id: t.name for t in found.values()}
    hidden = {(h["table"], h["column"]) for h in current["hidden"]}
    keys = set()
    for rel in current["relationships"]:
        keys.add((rel["from_table"], rel["from_column"]))
        keys.add((rel["to_table"], rel["to_column"]))
    many = {r["from_table"] for r in current["relationships"] if r.get("active") and not r.get("error")}
    one = {r["to_table"] for r in current["relationships"] if r.get("active") and not r.get("error")}
    queries = {q["id"]: q for q in transform.state(project)["queries"]}

    def rows(table):
        if table.id in queries:
            return queries[table.id]["output"]["profile"]["rows"]
        return con.execute(f"SELECT count(*) FROM {table.sql}").fetchone()[0]

    tables = [
        {
            "id": t.id,
            "name": t.name,
            "kind": "date" if t.id == model.DATE_ID else "fact" if t.id in many and t.id not in one
            else "dimension" if t.id in one else "table",
            "rows": rows(t),
            "columns": [
                {"name": c.name, "type": c.type, "hidden": (t.id, c.name) in hidden, "key": (t.id, c.name) in keys}
                for c in t.columns
            ],
        }
        for t in found.values()
    ]

    measures = []
    for measure in current["measures"]:
        value, error = None, measure.get("error")
        if not error:
            try:
                value = model.evaluate(con, project, [measure["name"]])["rows"][0][0]
            except model.ModelError as exc:
                error = str(exc)
        label = model.describe_measure(measure, names)
        chosen = next((r for r in current["relationships"] if r["id"] == measure.get("use_relationship")), None)
        if chosen:
            label += f", by {names.get(chosen['from_table'], '?')}[{chosen['from_column']}]"
        measures.append({**measure, "label": label, "value": value, "error": error})

    date_table = None
    if current["date_table"]:
        date_table = dict(current["date_table"])
        if model.DATE_ID in found:
            low, high = con.execute(f"SELECT min(\"Date\"), max(\"Date\") FROM {found[model.DATE_ID].sql}").fetchone()
            date_table.update(first=low.isoformat(), last=high.isoformat())

    return {
        "tables": tables,
        "relationships": [
            {**r, "from_name": names.get(r["from_table"], "?"), "to_name": names.get(r["to_table"], "?")}
            for r in current["relationships"]
        ],
        "measures": measures,
        "date_table": date_table,
        "layout": current.get("layout", {}),
        "can_undo": bool(current["history"]),
    }


def build_router(
    store: ProjectStore,
    settings_store: SettingsStore,
    client_factory: Callable[[ProviderSettings], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/datasets/{project_id}/model")

    def respond(project_id: str, change=None) -> dict:
        """Apply `change(project, con)` (if any) as one locked edit, re-check the model and return it."""
        try:
            with store.edit(project_id) as project, store.connect(project_id) as con:
                if transform.needs_apply(project):
                    transform.apply_all(con, project)
                model.state(project)
                if change:
                    change(project, con)
                model.revalidate(con, project)
                return view(con, project)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except model.ModelError as exc:
            raise HTTPException(400, str(exc)) from exc

    def item(project: dict, kind: str, item_id: str) -> dict:
        for entry in model.state(project)[kind]:
            if entry["id"] == item_id:
                return entry
        raise model.ModelError("Not found.")

    def undoable(action, summary=None, log_only=False):
        """Snapshot for undo, apply, and log. `summary` may be a function of the project, called before the action."""
        def change(project, con):
            model.snapshot(project)
            text = summary(project) if callable(summary) else summary
            action(project, con)
            if text:
                (activity.log if log_only else activity.changed)(project, "model", text)
        return change

    def rel_label(p, rel_id):
        rel = item(p, "relationships", rel_id)
        names = transform.query_names(p) | {model.DATE_ID: model.DATE_NAME}
        return f"{names.get(rel['from_table'])}[{rel['from_column']}] → {names.get(rel['to_table'])}[{rel['to_column']}]"

    @router.get("")
    def get_model(project_id: str):
        return respond(project_id)

    @router.post("/relationships")
    def add_relationship(project_id: str, body: RelationshipBody):
        return respond(project_id, undoable(lambda p, con: model.add_relationship(con, p, body.model_dump(), "Approved"),
                                            f"Added relationship {body.from_table}[{body.from_column}] → {body.to_table}[{body.to_column}]"))

    @router.post("/relationships/check")
    def check_relationship(project_id: str, body: RelationshipBody):
        """What a relationship would look like, for the hint shown while dragging one column onto another."""
        try:
            project = store.get(project_id)
            with store.connect(project_id) as con:
                return {"ok": True, **model.check_relationship_only(con, project, body.model_dump())}
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except model.ModelError as exc:
            return {"ok": False, "error": str(exc)}

    @router.delete("/relationships/{rel_id}")
    def remove_relationship(project_id: str, rel_id: str):
        return respond(project_id, undoable(lambda p, con: model.state(p)["relationships"].remove(item(p, "relationships", rel_id)),
                                            lambda p: f"Removed relationship {rel_label(p, rel_id)}"))

    @router.post("/measures")
    def add_measure(project_id: str, body: MeasureBody):
        return respond(project_id, undoable(lambda p, con: model.add_measure(con, p, body.model_dump(), "Approved"),
                                            f"Added measure {body.name}"))

    @router.post("/measures/check")
    def check_measure(project_id: str, body: MeasureTrialBody):
        """The formula bar's live check: is the measure valid, and what is its total? Nothing is saved."""
        try:
            project = store.get(project_id)
            with store.connect(project_id) as con:
                return {"ok": True, **model.try_measure(con, project, body.model_dump(exclude={"id"}), body.id)}
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except model.ModelError as exc:
            return {"ok": False, "error": str(exc)}

    @router.put("/measures/{measure_id}")
    def update_measure(project_id: str, measure_id: str, body: MeasureBody):
        return respond(project_id, undoable(lambda p, con: model.update_measure(con, p, measure_id, body.model_dump()),
                                            f"Changed measure {body.name}"))

    @router.put("/layout")
    def set_layout(project_id: str, body: LayoutBody):
        return respond(project_id, lambda p, con: model.set_layout(p, body.positions))

    @router.delete("/measures/{measure_id}")
    def remove_measure(project_id: str, measure_id: str):
        return respond(project_id, undoable(lambda p, con: model.state(p)["measures"].remove(item(p, "measures", measure_id)),
                                            lambda p: "Removed measure " + item(p, "measures", measure_id)["name"]))

    @router.put("/date-table")
    def set_date_table(project_id: str, body: DateTableBody):
        return respond(project_id, undoable(lambda p, con: model.set_date_table(con, p, body.start_year, body.end_year),
                                            "Set up the date table"))

    @router.delete("/date-table")
    def remove_date_table(project_id: str):
        def change(p, con):
            current = model.state(p)
            current["date_table"] = None
            current["relationships"] = [
                r for r in current["relationships"] if model.DATE_ID not in (r["from_table"], r["to_table"])
            ]
            model.build_date_table(con, p)
        return respond(project_id, undoable(change, "Removed the date table"))

    @router.post("/hidden")
    def set_hidden(project_id: str, body: HiddenBody):
        verb = "Hid" if body.hidden else "Showed"
        return respond(project_id, undoable(lambda p, con: model.set_hidden(con, p, body.table, body.column, body.hidden),
                                            f"{verb} {body.table}[{body.column}]"))

    @router.post("/approve")
    def approve(project_id: str, item_id: str | None = None):
        """Approve one relationship, measure or the date table by id, or everything the AI added."""
        def change(p, con):
            current = model.state(p)
            entries = current["relationships"] + current["measures"] + ([current["date_table"]] if current["date_table"] else [])
            for entry in entries:
                if entry.get("status") == "AI" and (item_id is None or entry.get("id", "date") == item_id):
                    entry["status"] = "Approved"
        text = "Approved AI changes" if item_id is None else "Approved an AI change"
        return respond(project_id, undoable(change, text, log_only=True))

    @router.post("/undo")
    def undo(project_id: str):
        def change(p, con):
            model.undo(p)
            model.build_date_table(con, p)
            activity.changed(p, "model", "Undid the last Model change")
        return respond(project_id, change)

    @router.post("/evaluate")
    def evaluate(project_id: str, body: EvaluateBody):
        try:
            project = store.get(project_id)
            with store.connect(project_id) as con:
                return model.evaluate(con, project, body.measures, body.group_by, body.filters, max(1, min(body.limit, model.MAX_GROUPS)))
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except model.ModelError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/ai")
    def ai_model_pass(project_id: str):
        settings = settings_store.load()
        if not settings.api_key:
            raise HTTPException(400, "Add an API key in Settings to let the AI build the model.")
        respond(project_id)  # bring Transform output and the model up to date first
        project = store.get(project_id)
        try:
            with store.connect(project_id) as con:
                proposal = ai_model.propose(guidance.guided(client_factory(settings), guidance.for_project(store, project)), settings.model, settings.capabilities, con, project)
        except llm.AIError as exc:
            raise HTTPException(502, f"AI modeling failed: {exc}") from exc
        result = {}
        def apply(p, con):
            result.update(ai_model.apply(con, p, proposal))
            if result["added"]:
                activity.changed(p, "model", f"AI built the model: {len(result['added'])} items added", "AI")

        body = respond(project_id, apply)
        return {**result, **body}

    router.calls = {"ai_model": ai_model_pass, "approve_model": approve}  # for Autopilot
    return router
