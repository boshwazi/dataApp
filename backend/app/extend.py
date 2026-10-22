"""Extending a dataset: build on a published dataset and add your own data on top.

The new dataset links every table of the one it extends (as a semantic model does), and takes its date table,
relationships and measures as inherited: read-only here, kept in step with the original. When the original
publishes, its tables are pulled again (refresh.pull_linked) and the inherited part of the model is rebuilt
from its new version. Your own sources, steps, relationships and measures sit alongside and are yours to change.
"""

from . import model, publishing, semantic, transform
from .projects import ProjectStore


class ExtendError(ValueError):
    pass


def create(store: ProjectStore, base_id: str, name: str) -> dict:
    """A new dataset extending `base_id`. Returns it."""
    dataset, notes = semantic.assemble(store, name, {base_id: None}, "extend")
    base = store.get(base_id)
    with store.edit(dataset["id"]) as project:
        project.pop("semantic", None)
        project["extends"] = {"id": base_id, "name": base["name"], "version": base["published"]["version"]}
        mark(project)
    return store.get(dataset["id"])


def mark(project: dict) -> None:
    """Everything in the model now is inherited (right after creating, or re-syncing, before the user's own items)."""
    current = model.state(project)
    for item in current["relationships"] + current["measures"]:
        item.setdefault("inherited", True)
    for source in project["sources"]:
        if source["origin"].get("dataset_id") == project["extends"]["id"]:
            source["inherited"] = True


def refuse_inherited(project: dict, kind: str, item_id: str) -> None:
    """Inherited items change where they come from, not here."""
    found = next((i for i in model.state(project)[kind] if i["id"] == item_id), None)
    if found and found.get("inherited"):
        raise model.ModelError(f"This comes from {project['extends']['name']}; change it there and it follows here.")


def sync(store: ProjectStore, project_id: str) -> None:
    """Rebuild the inherited relationships and measures from the original's published version.
    Your own items stay; an inherited measure whose name you've since used for your own is skipped."""
    project = store.get(project_id)
    base_id = (project.get("extends") or {}).get("id")
    if not base_id:
        return
    base = store.get(base_id)
    frozen = publishing.published_project(base)
    if frozen is None:
        return
    names = {(base_id, s["origin"]["query_id"]): s["table"] for s in project["sources"]
             if s["origin"].get("dataset_id") == base_id and s["origin"].get("query_id")}
    with store.edit(project_id) as project, store.connect(project_id) as con:
        transform.apply_all(con, project)
        current = model.state(project)
        current["relationships"] = [r for r in current["relationships"] if not r.get("inherited")]
        current["measures"] = [m for m in current["measures"] if not m.get("inherited")]
        own = ({id(r) for r in current["relationships"]}, {id(m) for m in current["measures"]})
        notes = {"conformed": [], "renamed": [], "skipped": []}
        semantic.carry_over(con, project, [(base, frozen, None)], names, notes)
        for item in current["relationships"]:
            if id(item) not in own[0]:
                item["inherited"] = True
        for item in current["measures"]:
            if id(item) not in own[1]:
                item["inherited"] = True
        model.revalidate(con, project)
        project["extends"]["version"] = base["published"]["version"]
