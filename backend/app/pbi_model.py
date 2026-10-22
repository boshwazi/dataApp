"""Reading Power BI files into one plain description of a semantic model.

  .pbix  model and data, read with PBIXRay (MIT). The file's compressed data is decoded, never run.
  .pbip  a project folder (or a zip of it): the model as TMDL text, no data.

Nothing in a file is executed: DAX and Power Query (M) are kept as text. Power BI's automatic
date tables (LocalDateTable_*, DateTableTemplate_*) are skipped.
"""

import io
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pandas as pd

TYPE_FROM_PBI = {"string": "text", "int64": "integer", "double": "decimal", "decimal": "decimal",
                 "datetime": "date", "date": "date", "boolean": "boolean", "currency": "decimal"}
AUTO_TABLES = re.compile(r"^(LocalDateTable_|DateTableTemplate_)")
AUTO_DATE = "__auto_date__"  # stands for Power BI's automatic date tables, which become datawazi's Date table
MAX_PROJECT_FILES = 2000


class PbiError(Exception):
    """The file can't be read; the message is safe to show."""


@dataclass
class PbiColumn:
    name: str
    type: str = "text"
    hidden: bool = False
    expression: str | None = None  # calculated column (DAX), kept as text
    description: str = ""
    is_key: bool = False


@dataclass
class PbiMeasure:
    name: str
    table: str
    expression: str
    format_string: str | None = None
    description: str = ""
    folder: str = ""
    hidden: bool = False


@dataclass
class PbiTable:
    name: str
    columns: list[PbiColumn] = field(default_factory=list)
    hidden: bool = False
    description: str = ""
    source_m: str | None = None      # Power Query, as text
    calculated: str | None = None    # calculated table (DAX), as text
    is_date_table: bool = False
    data: pd.DataFrame | None = None  # only from .pbix
    mode: str = "import"              # import, directquery, dual or directlake (Power BI storage mode)
    refreshed_at: str | None = None   # when Power BI last refreshed it (from .pbix)

    @property
    def connector(self) -> str:
        return connector_of(self.source_m, self.calculated)


@dataclass
class PbiRelationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    active: bool = True
    many_to_many: bool = False
    both_directions: bool = False


@dataclass
class PbiModel:
    name: str
    kind: str  # "pbix" or "pbip"
    tables: list[PbiTable] = field(default_factory=list)
    measures: list[PbiMeasure] = field(default_factory=list)
    relationships: list[PbiRelationship] = field(default_factory=list)
    roles: list[dict] = field(default_factory=list)

    def table(self, name: str) -> PbiTable | None:
        return next((t for t in self.tables if t.name.lower() == name.lower()), None)


CONNECTORS = [  # (pattern in Power Query, plain name); the first match wins
    ("Sql.Database", "SQL Server"), ("Sql.Databases", "SQL Server"), ("Oracle.Database", "Oracle"),
    ("PostgreSQL.Database", "PostgreSQL"), ("MySQL.Database", "MySQL"), ("Snowflake.Databases", "Snowflake"),
    ("Databricks", "Databricks"), ("GoogleBigQuery", "Google BigQuery"), ("AmazonRedshift", "Amazon Redshift"),
    ("Teradata", "Teradata"), ("SapHana", "SAP HANA"), ("SapBusinessWarehouse", "SAP BW"),
    ("AnalysisServices", "Analysis Services"), ("PowerPlatform.Dataflows", "Power BI dataflow"),
    ("PowerBI.Dataflows", "Power BI dataflow"), ("CommonDataService", "Dataverse"), ("Dataverse", "Dataverse"),
    ("Salesforce", "Salesforce"), ("SharePoint", "SharePoint"), ("AzureStorage", "Azure Storage"),
    ("AzureDataLakeStorage", "Azure Data Lake"), ("OData.Feed", "OData feed"), ("Odbc.", "ODBC"), ("OleDb.", "OLE DB"),
    ("Binary.Decompress", "Entered data"), ("Excel.Workbook", "Excel"), ("Csv.Document", "CSV"),
    ("Json.Document", "JSON"), ("Folder.Files", "Folder"), ("Web.Contents", "Web"), ("GoogleSheets", "Google Sheets"),
]


def connector_of(source_m: str | None, calculated: str | None = None) -> str:
    """The Power BI connector a table was loaded with, in plain words (Excel, SQL Server, SharePoint...)."""
    if calculated:
        return "Calculated in Power BI"
    for pattern, name in CONNECTORS:
        if source_m and pattern in source_m:
            if name == "Excel" and "sharepoint" in source_m.lower():
                return "Excel on SharePoint"
            return name
    return "Power Query" if source_m else "Unknown source"


MODES = {0: "import", 1: "directquery", 4: "dual", 5: "directlake"}


# .pbix


def read_pbix(data: bytes, name: str) -> PbiModel:
    from pbixray import PBIXRay  # imported here: only needed for .pbix

    try:
        entries = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    except zipfile.BadZipFile as exc:
        raise PbiError("This isn't a .pbix file, or it's damaged.") from exc
    if "DataModel" not in entries:
        if "Connections" in entries:
            raise PbiError("This report uses a live connection to a semantic model in the Power BI service, so the "
                           "file has no model or data inside. Open the model itself (or download it as a .pbix from "
                           "the service) and import that.")
        raise PbiError("This .pbix has no data model inside.")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "model.pbix"
        path.write_bytes(data)
        try:
            pbix = PBIXRay(str(path))
        except Exception as exc:  # the library raises many kinds on a broken or unsupported file
            raise PbiError("This .pbix file couldn't be read. It may be damaged, or from a version this app "
                           "doesn't support yet.") from exc
        model = PbiModel(name=name, kind="pbix")
        schema = pbix.schema
        m_by_table = {}
        try:
            for row in pbix.power_query.itertuples():
                m_by_table[row.TableName] = row.Expression
        except Exception:
            pass
        hidden_tables, descriptions = set(), {}
        try:
            for row in pbix.tmschema_tables.itertuples():
                if getattr(row, "IsHidden", 0):
                    hidden_tables.add(row.Name)
                if getattr(row, "Description", None):
                    descriptions[row.Name] = row.Description
        except Exception:
            pass
        calculated = {}
        try:
            for row in pbix.dax_tables.itertuples():
                calculated[row.TableName] = row.Expression
        except Exception:
            pass
        modes, refreshed = {}, {}
        try:
            for row in pbix.tmschema_partitions.itertuples():
                if row.Type in (3,):  # attribute hierarchy partitions, not data
                    continue
                modes.setdefault(row.TableName, MODES.get(int(row.Mode), "import"))
                when = pd.Timestamp(row.RefreshedTime) if row.RefreshedTime is not None else None
                if when is not None and when.year > 1900:
                    refreshed[row.TableName] = max(refreshed.get(row.TableName, when), when)
        except Exception:
            pass
        calc_columns = {}
        try:
            for row in pbix.dax_columns.itertuples():
                calc_columns[(row.TableName, row.ColumnName)] = row.Expression
        except Exception:
            pass
        for table_name in pbix.tables:
            if AUTO_TABLES.match(table_name):
                continue
            rows = schema[schema.TableName == table_name]
            columns = [PbiColumn(name=r.ColumnName, type=_pandas_type(r.PandasDataType),
                                 expression=calc_columns.get((table_name, r.ColumnName)))
                       for r in rows.itertuples() if not str(r.ColumnName).startswith("RowNumber-")]
            table = PbiTable(name=table_name, columns=columns, hidden=table_name in hidden_tables,
                             description=descriptions.get(table_name, ""), source_m=m_by_table.get(table_name),
                             calculated=calculated.get(table_name), mode=modes.get(table_name, "import"),
                             refreshed_at=refreshed[table_name].isoformat(timespec="minutes")
                             if table_name in refreshed else None)
            if table.mode == "directquery":
                table.data = None  # Power BI keeps no data for DirectQuery tables
            else:
                try:
                    table.data = pbix.get_table(table_name)
                except Exception:
                    table.data = None
            model.tables.append(table)
        for row in pbix.dax_measures.itertuples():
            model.measures.append(PbiMeasure(
                name=row.Name, table=row.TableName, expression=row.Expression or "",
                description=getattr(row, "Description", "") or "", folder=getattr(row, "DisplayFolder", "") or ""))
        _pbix_formats(pbix, model)
        for row in pbix.relationships.itertuples():
            if AUTO_TABLES.match(str(row.FromTableName)):
                continue
            to_table = AUTO_DATE if AUTO_TABLES.match(str(row.ToTableName)) else row.ToTableName
            model.relationships.append(PbiRelationship(
                from_table=row.FromTableName, from_column=row.FromColumnName, to_table=to_table,
                to_column="Date" if to_table == AUTO_DATE else row.ToColumnName, active=bool(row.IsActive),
                many_to_many=str(row.Cardinality) == "M:M",
                both_directions=str(row.CrossFilteringBehavior).lower().startswith("both")))
        try:
            for row in pbix.rls.itertuples():
                model.roles.append({"role": row.RoleName, "table": row.TableName, "filter": row.FilterExpression})
        except Exception:
            pass
        pbix.close() if hasattr(pbix, "close") else None
        return model


def _pandas_type(kind: str) -> str:
    kind = str(kind).lower()
    if kind.startswith("int"):
        return "integer"
    if kind.startswith("float") or "decimal" in kind:
        return "decimal"
    if kind.startswith("datetime"):
        return "date"
    if kind.startswith("bool"):
        return "boolean"
    return "text"


def _pbix_formats(pbix, model: PbiModel) -> None:
    """Format strings live in the model's metadata tables; join them onto the measures when present."""
    try:
        frame = pbix.tmschema_measures if hasattr(pbix, "tmschema_measures") else None
        if frame is None:
            return
        formats = {row.Name: getattr(row, "FormatString", None) for row in frame.itertuples()}
        for measure in model.measures:
            measure.format_string = formats.get(measure.name) or measure.format_string
    except Exception:
        return


# .pbip (TMDL)


def read_pbip(data: bytes, name: str) -> PbiModel:
    """A .pbip project uploaded as a zip: find its semantic model folder and read the TMDL."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PbiError("Upload the Power BI project as a .zip of its folder (with the .pbip file inside).") from exc
    names = archive.namelist()
    if len(names) > MAX_PROJECT_FILES:
        raise PbiError("The project has too many files.")
    tmdl = {}
    for entry in names:
        path = PurePosixPath(entry)
        if path.suffix == ".tmdl" and ".SemanticModel" in entry and "/definition/" in f"/{entry}":
            if ".." in path.parts or path.is_absolute():
                continue
            relative = entry.split("/definition/", 1)[1]
            tmdl[relative] = archive.read(entry).decode("utf-8-sig")
    if not tmdl:
        if any(n.endswith("model.bim") for n in names):
            raise PbiError("This project uses the older model.bim format. In Power BI Desktop, turn on "
                           "'Store semantic model using TMDL format' (Options > Preview features), save, and try again.")
        raise PbiError("No semantic model (TMDL) was found in this project.")
    return parse_tmdl(tmdl, name)


def parse_tmdl(files: dict[str, str], name: str) -> PbiModel:
    """`files` maps a path inside definition/ (model.tmdl, tables/X.tmdl, relationships.tmdl...) to its text."""
    model = PbiModel(name=name, kind="pbip")
    for _path, text in sorted(files.items()):
        for node in _tree(text):
            head = node["head"]
            if head.startswith("table "):
                _tmdl_table(model, node)
            elif head.startswith("relationship "):
                _tmdl_relationship(model, node)
            elif head.startswith("role "):
                for child in node["children"]:
                    if child["head"].startswith("tablePermission "):
                        table, _, expr = _name_and_rest(child["head"][len("tablePermission "):])
                        model.roles.append({"role": _name_and_rest(head[5:])[0], "table": table,
                                            "filter": expr or _expression(child)})
    return model


def _lines(text: str) -> list[tuple[int, str]]:
    out = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        stripped = raw.lstrip("\t ")
        indent = len(raw) - len(stripped)
        tabs = raw[:indent].count("\t") + raw[:indent].count(" ") // 4
        out.append((tabs, stripped.rstrip()))
    return out


def _tree(text: str) -> list[dict]:
    """Indentation into nodes: {head, indent, children, expr_lines, doc}. Multi-line expressions
    (a head ending in '=', or a ``` block) keep their raw lines as expr_lines."""
    lines = _lines(text)
    roots, stack, doc = [], [], []
    i = 0
    while i < len(lines):
        indent, content = lines[i]
        if not content:
            i += 1
            continue
        if content.startswith("///"):
            doc.append(content[3:].strip())
            i += 1
            continue
        node = {"head": content, "indent": indent, "children": [], "expr_lines": [], "doc": " ".join(doc)}
        doc = []
        i += 1
        if content.endswith("=") or content.endswith("= ```"):
            fence = content.endswith("```")
            while i < len(lines):
                nxt_indent, nxt = lines[i]
                if fence:
                    i += 1
                    if nxt.strip() == "```":
                        break
                    node["expr_lines"].append(nxt)
                    continue
                if nxt and nxt_indent < indent + 2:
                    break
                node["expr_lines"].append(nxt)
                i += 1
        while stack and stack[-1]["indent"] >= indent:
            stack.pop()
        (stack[-1]["children"] if stack else roots).append(node)
        stack.append(node)
    return roots


def _name_and_rest(text: str) -> tuple[str, str, str]:
    """Split "'My Table' = expr" or "Name = expr" into (name, '=', expr)."""
    text = text.strip()
    if text.startswith("'"):
        end, j = None, 1
        while j < len(text):
            if text[j] == "'" and (j + 1 >= len(text) or text[j + 1] != "'"):
                end = j
                break
            j += 2 if text[j] == "'" else 1
        name = text[1:end].replace("''", "'") if end else text.strip("'")
        rest = text[end + 1:].strip() if end else ""
    else:
        match = re.match(r"([^=]+?)\s*(=|$)(.*)", text)
        name, rest = match.group(1).strip(), (match.group(2) + match.group(3)).strip()
    if rest.startswith("="):
        return name, "=", rest[1:].strip()
    return name, "", rest


def _props(node: dict) -> dict:
    props = {}
    for child in node["children"]:
        head = child["head"]
        if ":" in head and not head.startswith(("column ", "measure ", "partition ", "hierarchy ", "annotation ")):
            key, value = head.split(":", 1)
            props[key.strip()] = value.strip()
        elif head in ("isHidden", "isKey", "isActive"):
            props[head] = "true"
    return props


def _expression(node: dict) -> str:
    return "\n".join(node["expr_lines"]).strip()


def _tmdl_table(model: PbiModel, node: dict) -> None:
    name = _name_and_rest(node["head"][len("table "):])[0]
    if AUTO_TABLES.match(name):
        return
    props = _props(node)
    table = PbiTable(name=name, hidden=props.get("isHidden") == "true", description=node["doc"],
                     is_date_table=props.get("dataCategory", "").lower() == "time")
    for child in node["children"]:
        head = child["head"]
        if head.startswith("column "):
            col_name, eq, expr = _name_and_rest(head[len("column "):])
            cprops = _props(child)
            table.columns.append(PbiColumn(
                name=col_name, type=TYPE_FROM_PBI.get(cprops.get("dataType", "string").lower(), "text"),
                hidden=cprops.get("isHidden") == "true", description=child["doc"], is_key=cprops.get("isKey") == "true",
                expression=(expr or _expression(child)) if eq else None))
        elif head.startswith("measure "):
            m_name, _, expr = _name_and_rest(head[len("measure "):])
            mprops = _props(child)
            model.measures.append(PbiMeasure(
                name=m_name, table=name, expression=expr or _expression(child),
                format_string=mprops.get("formatString"), description=child["doc"],
                folder=mprops.get("displayFolder", ""), hidden=mprops.get("isHidden") == "true"))
        elif head.startswith("partition "):
            _, _, kind = _name_and_rest(head[len("partition "):])
            mode = _props(child).get("mode", "import").lower()
            table.mode = {"directquery": "directquery", "dual": "dual", "directlake": "directlake"}.get(mode, "import")
            for sub in child["children"]:
                if sub["head"].startswith("source"):
                    text = _expression(sub) or sub["head"].split("=", 1)[-1].strip()
                    if kind.strip().lower() == "calculated":
                        table.calculated = text
                    else:
                        table.source_m = text
    model.tables.append(table)


def _column_ref(text: str) -> tuple[str, str]:
    """'Table Name'.'Column' or Table.Column."""
    text = text.strip()
    if text.startswith("'"):
        table, _, rest = _name_and_rest(text)
        rest = text[len(table.replace("'", "''")) + 2:].lstrip(".")
    else:
        table, _, rest = text.partition(".")
    column = rest.strip()
    if column.startswith("'") and column.endswith("'"):
        column = column[1:-1].replace("''", "'")
    return table.strip(), column


def _tmdl_relationship(model: PbiModel, node: dict) -> None:
    props = _props(node)
    if "fromColumn" not in props or "toColumn" not in props:
        return
    from_table, from_column = _column_ref(props["fromColumn"])
    to_table, to_column = _column_ref(props["toColumn"])
    if AUTO_TABLES.match(from_table):
        return
    if AUTO_TABLES.match(to_table):
        to_table, to_column = AUTO_DATE, "Date"
    model.relationships.append(PbiRelationship(
        from_table=from_table, from_column=from_column, to_table=to_table, to_column=to_column,
        active=props.get("isActive", "true").lower() != "false",
        many_to_many=props.get("toCardinality", "one").lower() == "many",
        both_directions=props.get("crossFilteringBehavior", "").lower() == "bothdirections"))
