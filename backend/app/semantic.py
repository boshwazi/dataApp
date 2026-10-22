"""Semantic models: one model over tables from several published datasets.

A semantic model is a dataset marked `semantic` whose tables are all linked from other datasets'
published versions (see links.py). Nothing is uploaded again, and each table follows its dataset:
when that dataset publishes, the semantic model pulls the new version (refresh.pull_linked).

Two ways to make one:

  - Build from tables: pick tables from any published datasets.
  - Merge models: take every table of two or more datasets.

Either way, what the source models already know is carried over through the engine's usual checks:
relationships between the chosen tables, the date table, and the measures whose tables came along.
A table with the same name and columns in two datasets (a shared Customers or Products table) is
kept once, so both models' relationships meet on it; same-named tables that differ are kept apart
under a longer name. A measure whose name is taken is renamed "Name (Dataset)", and formulas
follow the rename. Relationships across the source models are then proposed by the Model AI, as
for any dataset, and checked before they're added.
"""

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import activity, links, model, publishing, transform
from .projects import NotFound, ProjectStore, now

MAX_TABLES = 40


class SemanticError(ValueError):
    pass


def usable_tables(frozen: dict) -> list[dict]:
    return [q for q in transform.state(frozen)["queries"] if q.get("output") and not q.get("error")]


def columns_of(query: dict) -> list[str]:
    return [c["name"] for c in query["output"]["columns"]]


def published(store: ProjectStore, dataset_id: str) -> tuple[dict, dict]:
    try:
        dataset = store.get(dataset_id)
    except NotFound as exc:
        raise SemanticError("One of the datasets no longer exists.") from exc
    frozen = publishing.published_project(dataset)
    if frozen is None:
        raise SemanticError(f"{dataset['name']} isn't published yet. Publish it first, so its tables can be linked.")
    if dataset.get("semantic"):
        raise SemanticError(f"{dataset['name']} is a semantic model itself; pick the datasets it's built from instead.")
    return dataset, frozen


def assemble(store: ProjectStore, name: str, picks: dict[str, list[str] | None], how: str) -> tuple[dict, dict]:
    """A new semantic model with the picked tables of each dataset (None: all of them), and what each model knew.

    Returns (the new dataset, notes: conformed tables, renamed measures, what couldn't come along)."""
    if not name.strip():
        raise SemanticError("Give the semantic model a name.")
    sources = [(*published(store, dataset_id), wanted) for dataset_id, wanted in picks.items()]
    chosen = []  # (dataset, frozen, query)
    for dataset, frozen, wanted in sources:
        tables = usable_tables(frozen)
        if wanted is not None:
            by_ref = {q["id"]: q for q in tables} | {q["name"].lower(): q for q in tables}
            missing = [t for t in wanted if t not in by_ref and t.lower() not in by_ref]
            if missing:
                raise SemanticError(f"{dataset['name']} has no usable table called {', '.join(missing)}.")
            tables = list({id(q): q for q in (by_ref.get(t) or by_ref[t.lower()] for t in wanted)}.values())
        chosen += [(dataset, frozen, q) for q in tables]
    if not chosen:
        raise SemanticError("Pick at least one table.")
    if len(chosen) > MAX_TABLES:
        raise SemanticError(f"A semantic model can hold up to {MAX_TABLES} tables.")

    project = store.create(name)
    try:
        return fill(store, project["id"], sources, chosen, how)
    except Exception:
        store.delete(project["id"])  # nothing half-built is left behind
        raise


def fill(store: ProjectStore, project_id: str, sources: list, chosen: list, how: str) -> tuple[dict, dict]:
    notes = {"conformed": [], "renamed": [], "skipped": []}
    names: dict[tuple[str, str], str] = {}  # (dataset id, query id) -> table name in the new model
    kept: dict[str, tuple[list[str], str, str]] = {}  # lower name -> (columns, table name, from dataset)
    for dataset, _frozen, query in chosen:
        key = query["name"].lower()
        same = kept.get(key)
        if same and same[0] == columns_of(query):
            names[(dataset["id"], query["id"])] = same[1]
            notes["conformed"].append(f"{query['name']} is in {same[2]} and {dataset['name']} with the same columns: kept once")
            continue
        table, origin = links.read(store, dataset["id"], query["id"])
        wanted_name = query["name"] if not same else f"{query['name']} {dataset['name']}"
        source = store.add_source(project_id, table.frame, wanted_name, origin, table.warnings, table.types)
        names[(dataset["id"], query["id"])] = source["table"]
        kept.setdefault(key, (columns_of(query), source["table"], dataset["name"]))

    with store.edit(project_id) as fresh, store.connect(project_id) as con:
        fresh["semantic"] = {"how": how, "from": [{"id": d["id"], "name": d["name"]} for d, _, _ in sources],
                             "built_at": now()}
        transform.state(fresh)
        transform.apply_all(con, fresh)
        carry_over(con, fresh, sources, names, notes)
        model.revalidate(con, fresh)
        summary = f"{len(fresh['sources'])} tables from " + ", ".join(d["name"] for d, _, _ in sources)
        activity.changed(fresh, "model", f"Built a semantic model: {summary}")
    return store.get(project_id), notes


def carry_over(con, project: dict, sources: list, names: dict, notes: dict) -> None:
    """Each source model's date table, relationships and measures, for the tables that came along."""
    if any(model.state(frozen)["date_table"] for _, frozen, _ in sources):
        model.set_date_table(con, project)
    measure_names = set()
    for dataset, frozen, _ in sources:
        old = model.state(frozen)

        def table_name(ref, dataset=dataset):
            return model.DATE_ID if ref == model.DATE_ID else names.get((dataset["id"], ref))

        rel_ids = {}
        for rel in old["relationships"]:
            ends = table_name(rel["from_table"]), table_name(rel["to_table"])
            if rel.get("error") or None in ends:
                continue
            try:
                new = model.add_relationship(con, project, {"from_table": ends[0], "from_column": rel["from_column"],
                                                            "to_table": ends[1], "to_column": rel["to_column"],
                                                            "reason": rel.get("reason") or f"From {dataset['name']}"},
                                             "Approved")
                rel_ids[rel["id"]] = new["id"]
            except model.ModelError as exc:
                if "already" not in str(exc):  # the same relationship from the other model is fine
                    notes["skipped"].append(f"{dataset['name']}: relationship {rel['from_column']} → {rel['to_column']} ({exc})")

        renames = {}
        for measure in old["measures"]:
            if measure["name"].lower() in measure_names:
                renames[measure["name"]] = f"{measure['name']} ({dataset['name']})"
            measure_names.add(renames.get(measure["name"], measure["name"]).lower())

        def renamed(text, renames=renames):
            for before, after in renames.items():
                text = re.sub(r"\[\s*" + re.escape(before) + r"\s*\]", f"[{after}]", text, flags=re.IGNORECASE)
            return text

        pending = []
        for measure in old["measures"]:
            if measure.get("error"):
                continue
            proposal = {k: measure.get(k) for k in ("kind", "aggregation", "expression", "calc", "months", "format",
                                                     "description", "folder")}
            proposal["name"] = renames.get(measure["name"], measure["name"])
            proposal["reason"] = f"From {dataset['name']}"
            if measure.get("table"):
                proposal["table"] = table_name(measure["table"])
                if proposal["table"] is None:
                    continue  # its table wasn't picked
            if measure["kind"] == "formula" and measure.get("expression"):
                proposal["expression"] = renamed(measure["expression"])
            if measure.get("base"):
                proposal["base"] = renames.get(measure["base"], measure["base"])
            proposal["filters"] = [{**f, "table": table_name(f["table"])} if f.get("table") else dict(f)
                                   for f in measure.get("filters") or []]
            if any(f.get("table", "") is None for f in proposal["filters"]):
                notes["skipped"].append(f"{dataset['name']}: {proposal['name']} filters on a table that wasn't picked")
                continue
            proposal["use_relationship"] = rel_ids.get(measure.get("use_relationship") or "")
            pending.append(proposal)
            if proposal["name"] != measure["name"]:
                notes["renamed"].append(f"{measure['name']} from {dataset['name']} is now {proposal['name']}")
        # Formulas can refer to measures listed after them: try twice.
        for _attempt in range(2):
            failed = []
            for proposal in pending:
                try:
                    model.add_measure(con, project, proposal, "Approved")
                except (model.ModelError, KeyError, TypeError) as exc:
                    failed.append((proposal, str(exc)))
            pending = [p for p, _ in failed]
            if not pending:
                break
        notes["skipped"] += [f"{dataset['name']}: {p['name']} ({error})" for p, error in failed] if pending else []


# API


class TablePick(BaseModel):
    dataset_id: str
    table: str


class BuildBody(BaseModel):
    name: str
    tables: list[TablePick]


class MergeBody(BaseModel):
    name: str
    dataset_ids: list[str]


def build_router(store: ProjectStore, view) -> APIRouter:
    router = APIRouter(prefix="/api/semantic-models")

    def made(dataset: dict, notes: dict) -> dict:
        return {"dataset": view(dataset), "notes": notes}

    @router.get("/catalogue")
    def catalogue():
        """Published datasets and their clean tables, to build from (semantic models themselves left out)."""
        semantic = {d["id"] for d in store.all() if d.get("semantic")}
        return [d for d in links.catalogue(store, "") if d["dataset_id"] not in semantic]

    @router.post("")
    def build(body: BuildBody):
        picks: dict[str, list[str]] = {}
        for pick in body.tables:
            picks.setdefault(pick.dataset_id, []).append(pick.table)
        try:
            return made(*assemble(store, body.name, picks, "tables"))
        except (SemanticError, links.LinkError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/merge")
    def merge(body: MergeBody):
        ids = list(dict.fromkeys(body.dataset_ids))
        if len(ids) < 2:
            raise HTTPException(400, "Pick at least two datasets to merge.")
        try:
            return made(*assemble(store, body.name, {i: None for i in ids}, "merge"))
        except (SemanticError, links.LinkError) as exc:
            raise HTTPException(400, str(exc)) from exc

    return router
