"""Linked tables: clean tables copied from other datasets' published versions.

A linked table is a source like a file or a sheet, but it comes from another dataset's
Transform output, for example a certified Customers table. The copy is read from that
dataset's frozen published file, read-only, by the app (never by AI-written SQL), and keeps
its column types. The link remembers which version it copied, so the table shows
"update available" when the other dataset publishes again, and a refresh copies the new
version through the usual checks.
"""

from . import ingest, publishing, transform
from .projects import NotFound, ProjectStore, quote


class LinkError(ValueError):
    pass


def catalogue(store: ProjectStore, exclude_id: str) -> list[dict]:
    """Every published dataset's clean tables, certified datasets first."""
    found = []
    for dataset in store.all():
        if dataset["id"] == exclude_id or not dataset.get("published"):
            continue
        snapshot = dataset["published"]["snapshot"]
        tables = [
            {"query_id": q["id"], "name": q["name"], "rows": q["output"]["profile"]["rows"],
             "columns": [c["name"] for c in q["output"]["columns"]]}
            for q in snapshot["transform"]["queries"] if q.get("output") and not q.get("error")
        ]
        if tables:
            found.append({"dataset_id": dataset["id"], "dataset_name": dataset["name"],
                          "certified": bool(dataset.get("certified")), "version": dataset["published"]["version"],
                          "tables": tables})
    return sorted(found, key=lambda d: (not d["certified"], d["dataset_name"].lower()))


def read(store: ProjectStore, dataset_id: str, table_ref: str) -> tuple[ingest.LoadedTable, dict]:
    """Copy a clean table out of a dataset's published version, with an origin describing the link."""
    try:
        dataset = store.get(dataset_id)
    except NotFound as exc:
        raise LinkError("The dataset to link from no longer exists.") from exc
    frozen = publishing.published_project(dataset)
    if frozen is None:
        raise LinkError(f"{dataset['name']} has no published version to link to.")
    queries = transform.state(frozen)["queries"]
    query = next((q for q in queries if q["id"] == table_ref), None) or next(
        (q for q in queries if isinstance(table_ref, str) and q["name"].lower() == table_ref.strip().lower()), None)
    if query is None or not query.get("output") or query.get("error"):
        raise LinkError(f"{dataset['name']} has no usable table called '{table_ref}'.")
    with store.connect_published(dataset_id, dataset["published"]["file"]) as con:
        table = f"{transform.OUTPUT_SCHEMA}.{quote(query['name'])}"
        types = {name: kind for name, kind, *_ in con.execute(f"DESCRIBE {table}").fetchall()}
        frame = con.execute(f"SELECT * FROM {table}").fetchdf()
    loaded = ingest.LoadedTable(query["name"], frame, types=types)
    origin = {
        "kind": "dataset",
        "dataset_id": dataset_id,
        "dataset_name": dataset["name"],
        "query_id": query["id"],
        "table": query["name"],
        "version": dataset["published"]["version"],
        "label": f"{query['name']} from {dataset['name']}",
    }
    return loaded, origin


def latest_versions(store: ProjectStore) -> dict[str, int]:
    return {d["id"]: d["published"]["version"] for d in store.all() if d.get("published")}


def update_available(source: dict, versions: dict[str, int]) -> bool:
    origin = source["origin"]
    if origin.get("kind") != "dataset":
        return False
    latest = versions.get(origin.get("dataset_id"))
    return latest is not None and latest > (origin.get("version") or 0)


def linked_by(store: ProjectStore, dataset_id: str) -> list[dict]:
    """Datasets holding tables linked from this one."""
    users = []
    for dataset in store.all():
        tables = [s["table"] for s in dataset["sources"]
                  if s["origin"].get("kind") == "dataset" and s["origin"].get("dataset_id") == dataset_id]
        if tables:
            users.append({"id": dataset["id"], "name": dataset["name"], "tables": tables})
    return users
