"""HTTP routes for the stage chat agents.

The AI is asked without holding any lock. Its actions are then checked or applied inside
a locked edit, against the latest state, so a slow AI reply can't overwrite other changes.
"""

from contextlib import contextmanager
from typing import Callable, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import activity, ask, chat, llm, model, publishing, report, transform
from .dashboards import DashboardStore
from .projects import NotFound, ProjectStore
from .settings import ProviderSettings, SettingsStore


class ChatBody(BaseModel):
    message: str
    mode: Literal["full", "review"] = "full"
    page_id: str | None = None  # the Report chat: the page on show and its slicer choices
    filters: list[dict] = []


def build_router(
    datasets: ProjectStore,
    boards: DashboardStore,
    settings_store: SettingsStore,
    client_factory: Callable[[ProviderSettings], object],
    fetch_url: Callable,
) -> APIRouter:
    router = APIRouter()

    def settings():
        current = settings_store.load()
        if not current.api_key:
            raise HTTPException(400, "Add an API key in Settings to chat with the AI.")
        return current

    def stage_of(name: str) -> chat.Stage:
        if name not in activity.STAGES:
            raise HTTPException(404, "Unknown stage.")
        return chat.STAGES[name]

    def load(project_id: str) -> dict:
        try:
            return datasets.get(project_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    def summary_of(results: list[dict]) -> str:
        labels = [r["label"] for r in results if r["ok"]]
        return "; ".join(labels[:6]) + (f" (+{len(labels) - 6} more)" if len(labels) > 6 else "")

    # Dataset stages

    def read_context(project_id: str, stage: chat.Stage) -> tuple[dict, dict]:
        """The stage's state and its upstream stage's state, read without a lock."""
        project = load(project_id)
        if transform.needs_apply(project):
            with datasets.edit(project_id) as fresh, datasets.connect(project_id) as con:
                transform.apply_all(con, fresh)
            project = load(project_id)
        with datasets.connect(project_id) as con:
            ctx = chat.Ctx(project, con, True, datasets, fetch_url)
            state = stage.context(ctx)
            upstream = {}
            for name in chat.UPSTREAM[stage.name]:
                context = chat.STAGES[name].context(ctx)
                context.pop("operation_catalogue", None)
                upstream[activity.STAGE_NAMES[name]] = context
        return state, upstream

    def apply(project_id: str, stage: chat.Stage, actions: list[dict]) -> list[dict]:
        """Run actions for real and log them. Get Data actions lock per source; the others lock the dataset."""
        if stage.name == "get_data":
            results = chat.run_actions(stage, chat.Ctx(load(project_id), None, False, datasets, fetch_url), actions)
            with datasets.edit(project_id) as project:
                if any(r["ok"] for r in results):
                    activity.changed(project, stage.name, "Chat: " + summary_of(results), "AI", "chat")
            return results
        with datasets.edit(project_id) as project, datasets.connect(project_id) as con:
            results = chat.run_actions(stage, chat.Ctx(project, con, False, datasets, fetch_url), actions)
            if any(r["ok"] for r in results):
                activity.changed(project, stage.name, "Chat: " + summary_of(results), "AI", "chat")
        return results

    def check(project_id: str, stage: chat.Stage, actions: list[dict]) -> list[dict]:
        with datasets.connect(project_id) as con:
            return chat.check_actions(stage, chat.Ctx(load(project_id), con, True, datasets, fetch_url), actions)

    def messages(project_id: str, stage: str) -> list[dict]:
        return load(project_id).get("chats", {}).get(stage, [])

    def save_messages(project_id: str, stage: str, *entries: dict) -> list[dict]:
        with datasets.edit(project_id) as project:
            thread = project.setdefault("chats", {}).setdefault(stage, [])
            chat.append(thread, *entries)
            return list(thread)

    @router.get("/api/datasets/{project_id}/chat/{stage}")
    def get_dataset_chat(project_id: str, stage: str):
        stage_of(stage)
        return {"messages": messages(project_id, stage)}

    @router.post("/api/datasets/{project_id}/chat/{stage}")
    def dataset_chat(project_id: str, stage: str, body: ChatBody):
        current = stage_of(stage)
        config = settings()
        text = body.message.strip()
        if not text:
            raise HTTPException(400, "Type a message first.")
        state, upstream = read_context(project_id, current)
        history = messages(project_id, stage)
        try:
            answer = chat.ask(client_factory(config), config.model, config.capabilities, current, state, upstream, history, text,
                              body.mode)
        except llm.AIError as exc:
            raise HTTPException(502, f"The AI did not answer: {exc}") from exc
        user = chat.message("user", text)
        if not answer["actions"]:
            return {"messages": save_messages(project_id, stage, user, chat.message("assistant", answer["reply"])), "applied": False}
        if chat.needs_approval(current, answer["actions"], body.mode):
            results = check(project_id, current, answer["actions"])
            change = {"id": chat.new_id(), "status": "pending", "mode": body.mode, "actions": results}
            reply = chat.message("assistant", answer["reply"], change)
            return {"messages": save_messages(project_id, stage, user, reply), "applied": False}
        results = apply(project_id, current, answer["actions"])
        change = {"id": chat.new_id(), "status": "applied", "mode": body.mode, "actions": results}
        thread = save_messages(project_id, stage, user, chat.message("assistant", answer["reply"], change))
        return {"messages": thread, "applied": any(r["ok"] for r in results)}

    @router.post("/api/datasets/{project_id}/chat/{stage}/changes/{change_id}/{decision}")
    def decide(project_id: str, stage: str, change_id: str, decision: Literal["approve", "reject"]):
        current = stage_of(stage)
        try:
            pending = chat.find_change(messages(project_id, stage), change_id)
        except chat.ChatError as exc:
            raise HTTPException(404, str(exc)) from exc
        if pending["status"] != "pending":
            raise HTTPException(400, f"This change was already {pending['status']}.")
        results = None
        if decision == "approve":
            results = apply(project_id, current, [a for a in pending["actions"]])
        with datasets.edit(project_id) as project:
            thread = project.setdefault("chats", {}).setdefault(stage, [])
            change = chat.find_change(thread, change_id)
            change["status"] = "applied" if decision == "approve" else "rejected"
            if results is not None:
                change["actions"] = results
            else:
                activity.log(project, stage, "Rejected a chat change", via="chat")
            return {"messages": list(thread), "applied": bool(results and any(r["ok"] for r in results))}

    # Dashboard (report) chat

    @contextmanager
    def dashboard_scope(dashboard: dict):
        try:
            dataset = datasets.get(dashboard["dataset_id"])
        except NotFound as exc:
            raise HTTPException(409, "This dashboard's dataset was removed.") from exc
        frozen = publishing.published_project(dataset)
        if frozen is None:
            raise HTTPException(400, "Publish the dataset first: dashboards read its published version.")
        with datasets.connect_published(dataset["id"], dataset["published"]["file"]) as con:
            yield dataset, frozen, con

    def board(dashboard_id: str) -> dict:
        try:
            return boards.get(dashboard_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.get("/api/dashboards/{dashboard_id}/chat")
    def get_dashboard_chat(dashboard_id: str):
        return {"messages": board(dashboard_id).get("chat", [])}

    def apply_report(dashboard_id: str, actions: list[dict]) -> list[dict]:
        with boards.edit(dashboard_id) as dash, dashboard_scope(dash) as (dataset, frozen, con):
            ctx = chat.Ctx(publishing.bind(frozen, dash["draft"]), con, False)
            results = chat.run_actions(chat.REPORT, ctx, actions)
            if any(r["ok"] for r in results):
                activity.log(dash, "report", "Chat: " + summary_of(results), "AI", "chat")
                activity.dashboard_reviewed(dash, dataset)
        return results

    def save_dashboard_messages(dashboard_id: str, *entries: dict) -> list[dict]:
        with boards.edit(dashboard_id) as dash:
            thread = dash.setdefault("chat", [])
            chat.append(thread, *entries)
            return list(thread)

    @router.post("/api/dashboards/{dashboard_id}/chat")
    def dashboard_chat(dashboard_id: str, body: ChatBody):
        config = settings()
        text = body.message.strip()
        if not text:
            raise HTTPException(400, "Type a message first.")
        dash = board(dashboard_id)
        with dashboard_scope(dash) as (dataset, frozen, con):
            project = publishing.bind(frozen, dash["draft"])
            page = next((p for p in dash["draft"]["pages"] if p["id"] == body.page_id), None)
            try:
                chosen = report.check_filters(con, frozen, body.filters) if body.filters else []
            except (report.ReportError, model.ModelError) as exc:
                raise HTTPException(400, str(exc)) from exc
            project[model.PAGE_FILTERS] = list((page or {}).get("filters") or []) + chosen
            scope = report.describe_filters(con, frozen, project[model.PAGE_FILTERS])
            ctx = chat.Ctx(project, con, True)
            state = chat.REPORT.context(ctx)
            if scope:
                state["filtered_to"] = scope  # the numbers in the context are for this selection
            client = client_factory(config)
            try:
                answer = chat.ask(client, config.model, config.capabilities, chat.REPORT, state, {},
                                  dash.get("chat", []), text, body.mode)
                # Numbers not on the dashboard: answered from the data model by Ask's checked pipeline.
                data_answer = None
                if answer["data_question"]:
                    history = [{"role": m["role"], "content": m["content"]} for m in dash.get("chat", [])[-6:]]
                    found = ask.ask(client, config.model, config.capabilities, con, project, dash["draft"]["pages"],
                                    answer["data_question"], history)
                    formats = {m["name"]: m["format"] for m in model.state(frozen)["measures"]}
                    data_answer = {**found, "question": answer["data_question"], "formats": formats,
                                   "dataset_version": dataset["published"]["version"], "as_of": report.as_of(con, frozen)}
            except llm.AIError as exc:
                raise HTTPException(502, f"The AI did not answer: {exc}") from exc
            if answer["actions"] and chat.needs_approval(chat.REPORT, answer["actions"], body.mode):
                results = chat.check_actions(chat.REPORT, ctx, answer["actions"])
        user = chat.message("user", text)
        if data_answer is not None:
            reply = chat.message("assistant", data_answer["answer"] or data_answer["cannot_answer"])
            reply["answer"] = data_answer
        else:
            reply = chat.message("assistant", answer["reply"])
            reply["check"] = chat.check_reply(answer["reply"], state)
            how = chat.explain_reply(answer["reply"], answer["used_visuals"], state)
            if how:
                reply["how"] = how
        if not answer["actions"]:
            return {"messages": save_dashboard_messages(dashboard_id, user, reply), "applied": False}
        if chat.needs_approval(chat.REPORT, answer["actions"], body.mode):
            reply["change"] = {"id": chat.new_id(), "status": "pending", "mode": body.mode, "actions": results}
            return {"messages": save_dashboard_messages(dashboard_id, user, reply), "applied": False}
        results = apply_report(dashboard_id, answer["actions"])
        reply["change"] = {"id": chat.new_id(), "status": "applied", "mode": body.mode, "actions": results}
        return {"messages": save_dashboard_messages(dashboard_id, user, reply), "applied": any(r["ok"] for r in results)}

    @router.post("/api/dashboards/{dashboard_id}/chat/changes/{change_id}/{decision}")
    def decide_dashboard(dashboard_id: str, change_id: str, decision: Literal["approve", "reject"]):
        try:
            pending = chat.find_change(board(dashboard_id).get("chat", []), change_id)
        except chat.ChatError as exc:
            raise HTTPException(404, str(exc)) from exc
        if pending["status"] != "pending":
            raise HTTPException(400, f"This change was already {pending['status']}.")
        results = apply_report(dashboard_id, pending["actions"]) if decision == "approve" else None
        with boards.edit(dashboard_id) as dash:
            thread = dash.setdefault("chat", [])
            change = chat.find_change(thread, change_id)
            change["status"] = "applied" if decision == "approve" else "rejected"
            if results is not None:
                change["actions"] = results
            else:
                activity.log(dash, "report", "Rejected a chat change", via="chat")
            return {"messages": list(thread), "applied": bool(results and any(r["ok"] for r in results))}

    return router
