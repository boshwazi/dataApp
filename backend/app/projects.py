"""Projects and their data sources, stored on local disk.

One folder per project:
  <root>/<project id>/project.json   name, sources and their profiles
  <root>/<project id>/data.duckdb    one table per source, loaded as raw text

Sources are loaded exactly as read (every column VARCHAR, blanks as NULL). Choosing
types and cleaning values are Transform steps, so they stay visible and undoable.

Everything goes through ProjectStore so the hosted phase can swap in Postgres and
object storage. Every record carries a workspace_id from day one.
"""

import json
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from . import profiling

LOCAL_WORKSPACE = "local"
ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")
MAX_NAME_LENGTH = 60


class NotFound(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def table_name(text: str) -> str:
    """A safe, lower-case SQL identifier from a file, sheet or user-given name."""
    name = re.sub(r"[^0-9a-zA-Z]+", "_", text.strip()).strip("_").lower()
    if not name:
        name = "table"
    if name[0].isdigit():
        name = f"t_{name}"
    return name[:MAX_NAME_LENGTH]


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class ProjectStore:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.RLock()

    # Projects

    def all(self) -> list[dict]:
        if not self.root.exists():
            return []
        projects = []
        for path in self.root.glob("*/project.json"):
            projects.append(json.loads(path.read_text(encoding="utf-8")))
        return sorted(projects, key=lambda p: p["updated_at"], reverse=True)

    def create(self, name: str) -> dict:
        project = {
            "id": new_id(),
            "workspace_id": LOCAL_WORKSPACE,
            "name": name.strip() or "Untitled project",
            "created_at": now(),
            "updated_at": now(),
            "sources": [],
        }
        self.save(project)
        return project

    def get(self, project_id: str) -> dict:
        path = self._folder(project_id) / "project.json"
        if not path.exists():
            raise NotFound("Project not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, project: dict) -> None:
        project["updated_at"] = now()
        folder = self._folder(project["id"])
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "project.json.tmp"
        with self._lock:
            tmp.write_text(json.dumps(project, indent=2), encoding="utf-8")
            tmp.replace(folder / "project.json")

    @contextmanager
    def edit(self, project_id: str):
        """Load, change and save a project as one step, so concurrent requests don't overwrite each other."""
        with self._lock:
            project = self.get(project_id)
            yield project
            self.save(project)

    def rename(self, project_id: str, name: str) -> dict:
        with self.edit(project_id) as project:
            project["name"] = name.strip() or project["name"]
        return project

    def delete(self, project_id: str) -> None:
        folder = self._folder(project_id)
        if not folder.exists():
            raise NotFound("Project not found.")
        shutil.rmtree(folder)

    # Sources

    def add_source(self, project_id: str, frame: pd.DataFrame, name: str, origin: dict, warnings: list[str]) -> dict:
        with self.edit(project_id) as project, self.connect(project_id) as con:
            table = unique(table_name(name), {s["table"] for s in project["sources"]})
            con.register("incoming", frame)
            con.execute(f"CREATE TABLE {quote(table)} AS SELECT * FROM incoming")
            con.unregister("incoming")
            source = {
                "id": new_id(),
                "workspace_id": project["workspace_id"],
                "table": table,
                "origin": origin,
                "added_at": now(),
                "warnings": warnings,
                "profile": profiling.profile_table(con, table),
                "ai": None,
            }
            project["sources"].append(source)
        return source

    def source(self, project: dict, source_id: str) -> dict:
        for source in project["sources"]:
            if source["id"] == source_id:
                return source
        raise NotFound("Source not found.")

    def rename_source(self, project_id: str, source_id: str, name: str) -> dict:
        with self.edit(project_id) as project:
            source = self.source(project, source_id)
            new_table = table_name(name)
            if new_table in {s["table"] for s in project["sources"] if s["id"] != source_id}:
                raise ValueError(f"A table named {new_table} already exists.")
            if new_table != source["table"]:
                with self.connect(project_id) as con:
                    con.execute(f"ALTER TABLE {quote(source['table'])} RENAME TO {quote(new_table)}")
                source["table"] = new_table
        return source

    def delete_source(self, project_id: str, source_id: str) -> None:
        with self.edit(project_id) as project:
            source = self.source(project, source_id)
            with self.connect(project_id) as con:
                con.execute(f"DROP TABLE IF EXISTS {quote(source['table'])}")
            project["sources"].remove(source)

    def reprofile(self, project_id: str, source_id: str) -> dict:
        with self.edit(project_id) as project, self.connect(project_id) as con:
            source = self.source(project, source_id)
            source["profile"] = profiling.profile_table(con, source["table"])
            source["ai"] = None  # the old description may no longer match
        return source

    def set_ai(self, project_id: str, source_id: str, ai: dict) -> dict:
        with self.edit(project_id) as project:
            source = self.source(project, source_id)
            source["ai"] = ai
        return source

    def preview(self, project: dict, source_id: str, limit: int = 100, offset: int = 0) -> dict:
        source = self.source(project, source_id)
        with self.connect(project["id"]) as con:
            result = con.execute(f"SELECT * FROM {quote(source['table'])} LIMIT ? OFFSET ?", [limit, offset])
            columns = [d[0] for d in result.description]
            rows = result.fetchall()
        return {"columns": columns, "rows": rows, "total": source["profile"]["rows"], "offset": offset}

    def sample_rows(self, project: dict, source_id: str, limit: int = 5) -> list[dict]:
        preview = self.preview(project, source_id, limit)
        return [dict(zip(preview["columns"], row)) for row in preview["rows"]]

    @contextmanager
    def connect(self, project_id: str):
        # No reading or writing other files or URLs from SQL: all data arrives through ingest.
        con = duckdb.connect(str(self._folder(project_id) / "data.duckdb"), config={"enable_external_access": False})
        try:
            yield con
        finally:
            con.close()

    def _folder(self, project_id: str) -> Path:
        if not ID_PATTERN.match(project_id):
            raise NotFound("Project not found.")
        return self.root / project_id


def unique(name: str, taken: set[str]) -> str:
    candidate, n = name, 2
    while candidate in taken:
        suffix = f"_{n}"
        candidate = name[: MAX_NAME_LENGTH - len(suffix)] + suffix
        n += 1
    return candidate
