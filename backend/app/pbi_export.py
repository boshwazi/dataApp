"""A dataset as a Power BI project (.pbip): the semantic model in TMDL, plus its data as Parquet.

  <Name>/<Name>.pbip
  <Name>/<Name>.SemanticModel/  definition.pbism, definition/ (database, model, expressions,
                                 relationships, tables/*.tmdl)
  <Name>/<Name>.Report/         definition.pbir and the report: a dashboard's pages (pbi_report.py), or one
                                 empty page when exporting the dataset on its own
  <Name>/data/*.parquet          one file per table, read through the DataFolder parameter
  <Name>/README.txt

Open the .pbip in Power BI Desktop, set DataFolder to the data folder, and Refresh. Save as .pbix
from there if a single file is needed. Measures are written as DAX from the catalogue.
"""

import io
import json
import re
import tempfile
import uuid
import zipfile
from pathlib import Path

import duckdb

from . import model, pbi_dax, pbi_report, transform

THEME_FILE = "datawazi.json"
# House style: Okabe-Ito colours (colour-blind safe), dark text on white, the app's blue for tables.
THEME = {"name": THEME_FILE, "dataColors": ["#0072B2", "#E69F00", "#009E73", "#56B4E9", "#D55E00", "#CC79A7",
                                            "#F0E442", "#000000"],
         "background": "#FFFFFF", "foreground": "#16202E", "tableAccent": "#1B5FD1",
         "good": "#007A5E", "bad": "#B54A00", "neutral": "#9CA3AF"}
PBI_TYPES = {"integer": "int64", "decimal": "double", "date": "dateTime", "boolean": "boolean", "text": "string"}


def safe_name(text: str) -> str:
    name = re.sub(r"[^\w\- ]", "", text or "").strip()
    return name[:60] or "datawazi model"


def quote(name: str) -> str:
    """A TMDL object name: bare when simple, otherwise in single quotes."""
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "'" + name.replace("'", "''") + "'"


def tag() -> str:
    return str(uuid.uuid4())


def doc(text: str, indent: str) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return "".join(f"{indent}/// {line.strip()}\n" for line in lines)


def logical(column) -> str:
    return column.type if column.type in PBI_TYPES else "text"


def table_tmdl(table, measures: list[dict], names: dict, hidden: set, is_date: bool, data_file: str) -> str:
    out = [f"table {quote(table.name)}\n", f"\tlineageTag: {tag()}\n"]
    if is_date:
        out.append("\tdataCategory: Time\n")
    out.append("\n")
    for m in measures:
        out.append(doc(m.get("description", ""), "\t"))
        dax = pbi_dax.from_measure(m, names.get(m.get("table")), names)
        out.append(f"\tmeasure {quote(m['name'])} = {dax}\n")
        out.append(f"\t\tformatString: {pbi_dax.format_string(m.get('format') or {})}\n")
        out.append(f"\t\tlineageTag: {tag()}\n\n")
    for column in table.columns:
        kind = logical(column)
        out.append(f"\tcolumn {quote(column.name)}\n")
        out.append(f"\t\tdataType: {PBI_TYPES[kind]}\n")
        if kind == "date":
            out.append("\t\tformatString: yyyy-mm-dd\n")
        if is_date and column.name == "Date":
            out.append("\t\tisKey\n")
        if (table.id, column.name) in hidden:
            out.append("\t\tisHidden\n")
        out.append(f"\t\tlineageTag: {tag()}\n")
        out.append(f"\t\tsummarizeBy: {'sum' if kind in ('integer', 'decimal') and not is_date else 'none'}\n")
        out.append(f"\t\tsourceColumn: {column.name}\n\n")
        out.append("\t\tannotation SummarizationSetBy = Automatic\n\n")
    out.append(f"\tpartition {quote(table.name)} = m\n")
    out.append("\t\tmode: import\n")
    out.append("\t\tsource =\n")
    out.append("\t\t\t\tlet\n")
    out.append(f'\t\t\t\t    Source = Parquet.Document(File.Contents(DataFolder & "{data_file}"))\n')
    out.append("\t\t\t\tin\n")
    out.append("\t\t\t\t    Source\n\n")
    out.append("\tannotation PBI_ResultType = Table\n")
    return "".join(out)


def build(con, project: dict, pages: list[dict] | None = None, title: str | None = None) -> tuple[str, dict[str, bytes]]:
    """The project's folder name and its files (path inside the folder → bytes). With `pages` (a dashboard's),
    the report holds them as Power BI pages; `title` names the project after the dashboard."""
    transform.apply_all(con, project) if transform.needs_apply(project) else None
    found = model.tables(con, project)
    state = model.state(project)
    name = safe_name(title or project["name"])
    names = {table_id: t.name for table_id, t in found.items()}
    hidden = {(h["table"], h["column"]) for h in state.get("hidden", [])}
    files: dict[str, bytes] = {}

    # Measures live in their aggregate's table; formulas go with the first measure they use.
    measures = [m for m in state["measures"] if not m.get("error")]
    rels = {r["id"]: r for r in state["relationships"]}
    measures = [
        {**m, "_uses": (names.get(r["from_table"]), r["from_column"], names.get(r["to_table"]), r["to_column"])}
        if (r := rels.get(m.get("use_relationship"))) else m
        for m in measures
    ]
    by_name = {m["name"].lower(): m for m in measures}
    homes: dict[str, list[dict]] = {t: [] for t in found}

    def home(m, seen=()):
        if m["kind"] == "aggregate":
            return m["table"]
        for ref in model.measure_refs(m):
            other = by_name.get(ref.lower())
            if other and other["name"] not in seen:
                return home(other, seen + (m["name"],))
        return next(iter(found))

    for m in measures:
        homes.setdefault(home(m), []).append(m)

    # Data: written by a separate in-memory DuckDB that runs only these fixed statements.
    with tempfile.TemporaryDirectory() as folder:
        writer = duckdb.connect()
        for table_id, table in found.items():
            frame = con.execute(f"SELECT * FROM {table.sql}").fetchdf()
            target = Path(folder) / f"{table_id}.parquet"
            writer.register("frame", frame)
            writer.execute(f"COPY (SELECT * FROM frame) TO '{target.as_posix()}' (FORMAT PARQUET)")
            writer.unregister("frame")
            files[f"data/{safe_file(table.name)}.parquet"] = target.read_bytes()
        writer.close()

    model_dir = f"{name}.SemanticModel/definition"
    files[f"{name}.SemanticModel/definition.pbism"] = json_bytes({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {"qnaEnabled": True}})
    files[f"{name}.SemanticModel/.platform"] = json_bytes(platform("SemanticModel", project["name"]))
    files[f"{model_dir}/database.tmdl"] = b"database\n\tcompatibilityLevel: 1600\n"
    refs = "".join(f"ref table {quote(t.name)}\n" for t in found.values())
    files[f"{model_dir}/model.tmdl"] = (
        "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n"
        "\tdataAccessOptions\n\t\tlegacyRedirects\n\t\treturnErrorValuesAsNull\n\n"
        "annotation __PBI_TimeIntelligenceEnabled = 0\n\n" + refs).encode()
    files[f"{model_dir}/expressions.tmdl"] = (
        "/// The folder holding this project's data files: the data folder next to the .pbip, ending with a backslash.\n"
        "/// Change it in Transform data > Edit parameters after moving the project.\n"
        f'expression DataFolder = "C:\\datawazi\\{name}\\data\\" meta [IsParameterQuery=true, Type="Text", '
        "IsParameterQueryRequired=true]\n"
        f"\tlineageTag: {tag()}\n\n\tannotation PBI_ResultType = Text\n").encode()
    for table_id, table in found.items():
        files[f"{model_dir}/tables/{safe_file(table.name)}.tmdl"] = table_tmdl(
            table, homes.get(table_id, []), names, hidden, table_id == model.DATE_ID,
            f"{safe_file(table.name)}.parquet").encode()
    relationships = []
    for r in state["relationships"]:
        if r.get("error") or r["from_table"] not in found or r["to_table"] not in found:
            continue
        lines = [f"relationship {tag()}\n",
                 f"\tfromColumn: {quote(names[r['from_table']])}.{quote(r['from_column'])}\n",
                 f"\ttoColumn: {quote(names[r['to_table']])}.{quote(r['to_column'])}\n"]
        if not r.get("active", True):
            lines.append("\tisActive: false\n")
        relationships.append("".join(lines))
    if relationships:
        files[f"{model_dir}/relationships.tmdl"] = "\n".join(relationships).encode()

    report = f"{name}.Report"
    files[f"{report}/definition.pbir"] = json_bytes({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}}})
    files[f"{report}/.platform"] = json_bytes(platform("Report", project["name"]))
    measure_table = {m["name"]: names[home] for home, ms in homes.items() if home in names for m in ms}
    report_part, notes = report_files(report, pages or [], measure_table, names)
    files.update(report_part)
    files[f"{name}.pbip"] = json_bytes({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": report}}], "settings": {"enableAutoRecovery": True}})
    files["README.txt"] = (readme(name, project, len(measures)) + (
        "\nThe report has the dashboard's pages. Left out:\n" + "".join(f"- {n}\n" for n in notes) if notes else "")).encode()
    return name, files


def safe_file(name: str) -> str:
    return re.sub(r"[^\w\- ]", "_", name).strip() or "table"


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode()


def platform(kind: str, display: str) -> dict:
    return {"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": kind, "displayName": display}, "config": {"version": "2.0", "logicalId": tag()}}


def report_files(report: str, pages: list[dict], measure_table: dict[str, str],
                 table_names: dict[str, str]) -> tuple[dict[str, bytes], list[str]]:
    """The report: a dashboard's pages as Power BI pages, or one empty page (Power BI Desktop needs one)."""
    base = f"{report}/definition"
    page_part, order, notes = pbi_report.page_files(base, pages, measure_table, table_names)
    if not order:
        page = uuid.uuid4().hex[:20]
        order = [page]
        page_part = {f"{base}/pages/{page}/page.json": json_bytes({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
            "name": page, "displayName": "Page 1", "displayOption": "FitToPage", "height": 720, "width": 1280})}
    return {
        f"{base}/version.json": json_bytes({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0"}),
        f"{base}/report.json": json_bytes({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
            # Our own theme (house-style colours), registered with the report, rather than Microsoft's base theme file.
            "themeCollection": {"customTheme": {"name": THEME["name"], "reportVersionAtImport": {
                "visual": "2.11.0", "report": "3.4.0", "page": "2.3.1"}, "type": "RegisteredResources"}},
            "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                                  "items": [{"name": THEME["name"], "path": THEME_FILE, "type": "CustomTheme"}]}],
            "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                         "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                         "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True}}),
        f"{report}/StaticResources/RegisteredResources/{THEME_FILE}": json_bytes(THEME),
        f"{base}/pages/pages.json": json_bytes({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": order, "activePageName": order[0]}),
        **page_part,
    }, notes


def readme(name: str, project: dict, measures: int) -> str:
    return f"""{project['name']}, exported from datawazi

1. Unzip this folder somewhere, for example C:\\datawazi\\{name}\\
2. Open {name}.pbip in Power BI Desktop (it needs the Power BI Project preview feature on older versions).
3. If you unzipped it anywhere else: Transform data > Edit parameters > DataFolder, and set it to the full
   path of the data folder in this project, ending with a backslash. Then click Refresh.
4. To get a single .pbix: File > Save as > Power BI file (.pbix).

The model has {measures} measures written in DAX, its relationships and the Date table (marked as a date
table). The data folder holds each table as a Parquet file: the clean data, after datawazi's steps.
"""


def zip_project(name: str, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(files.items()):
            archive.writestr(f"{name}/{path}", data)
    return buffer.getvalue()
