"""Custom instructions: what a workspace, a dataset or a dashboard asks of every AI that works on it.

Like a system prompt the user writes: "we report in RM", "our financial year starts in April",
"call customers clients". They go to the AI after its own rules, which still come first, and never
reach the engine's checks: an instruction can change what the AI proposes, not what is allowed.
"""

import hashlib

from .projects import FolderStore, NotFound

MAX_LENGTH = 2000


class PreferenceStore(FolderStore):
    """One small record per workspace, kept like datasets and dashboards (files locally, Postgres when hosted)."""

    filename = "preferences.json"
    label = "Preferences"

    def record_id(self) -> str:
        return hashlib.sha1(self.workspace().encode()).hexdigest()[:12]

    def load(self) -> dict:
        try:
            return self.get(self.record_id())
        except NotFound:
            return {"id": self.record_id(), "instructions": ""}

    def set_instructions(self, text: str) -> dict:
        record = self.load()
        record["instructions"] = clean(text)
        self.save(record)
        return record


def clean(text) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("Instructions are text.")
    text = text.strip()
    if len(text) > MAX_LENGTH:
        raise ValueError(f"Keep instructions under {MAX_LENGTH:,} characters.")
    return text


def compose(workspace: str = "", dataset: str = "", dashboard: str = "") -> str:
    """The block added to the AI's system prompt, or '' when nobody wrote anything."""
    parts = [(label, text.strip()) for label, text in
             (("For the whole workspace", workspace), ("For this dataset", dataset), ("For this dashboard", dashboard))
             if text and text.strip()]
    if not parts:
        return ""
    body = "\n".join(f"{label}:\n{text}" for label, text in parts)
    return ("\n\nInstructions from the people who use this app. Follow them where they apply. Your rules above, "
            "the data's facts and the checks still come first; if an instruction conflicts with them, say so.\n" + body)


def for_project(store, project: dict | None) -> str:
    workspace = store.preferences.load()["instructions"] if getattr(store, "preferences", None) else ""
    return compose(workspace, (project or {}).get("instructions", ""))


def for_dashboard(store, dashboard: dict, dataset: dict | None) -> str:
    workspace = store.preferences.load()["instructions"] if getattr(store, "preferences", None) else ""
    if dataset is None and dashboard.get("dataset_id"):
        try:
            dataset = store.get(dashboard["dataset_id"])
        except NotFound:
            dataset = None
    return compose(workspace, (dataset or {}).get("instructions", ""), dashboard.get("instructions", ""))


def guided(client, instructions: str):
    """The AI client with the instructions attached; llm.ask_json adds them to every system prompt."""
    try:
        client.datawazi_instructions = instructions
    except AttributeError:  # a client that takes no attributes simply goes without
        pass
    return client
