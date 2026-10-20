"""Reading CSV, Excel and Google Sheets into raw text tables.

Every value is kept as text exactly as it appears in the file, with empty cells as
NULL. Nothing is guessed or fixed here: profiling reports what it finds, and the
Transform stage decides what to change.
"""

import csv
import io
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
CSV_DELIMITERS = ",;\t|"
EXCEL_SUFFIXES = (".xlsx", ".xlsm")
CSV_SUFFIXES = (".csv", ".tsv", ".txt")


class IngestError(ValueError):
    pass


@dataclass
class LoadedTable:
    name: str
    frame: pd.DataFrame
    warnings: list[str] = field(default_factory=list)


def read_file(filename: str, data: bytes) -> list[LoadedTable]:
    lower = filename.lower()
    stem = re.sub(r"\.[^.]+$", "", filename)
    if lower.endswith(EXCEL_SUFFIXES):
        return read_excel(stem, data)
    if lower.endswith(CSV_SUFFIXES):
        return [read_csv(stem, data)]
    if lower.endswith(".xls"):
        raise IngestError(f"{filename}: old .xls files are not supported. Save it as .xlsx and try again.")
    raise IngestError(f"{filename}: only CSV and Excel (.xlsx) files are supported.")


def read_csv(name: str, data: bytes) -> LoadedTable:
    text = decode(data)
    if not text.strip():
        raise IngestError(f"{name}: the file is empty.")
    sep = sniff_delimiter(text)
    long_rows = 0

    def keep_first_fields(fields: list[str]) -> list[str]:
        nonlocal long_rows
        long_rows += 1
        return fields[:width]

    width = len(next(csv.reader(io.StringIO(text), delimiter=sep)))
    frame = pd.read_csv(
        io.StringIO(text),
        sep=sep,
        header=None,
        dtype=str,
        keep_default_na=False,
        engine="python",
        on_bad_lines=keep_first_fields,
    )
    warnings = []
    if long_rows:
        rows = "1 row" if long_rows == 1 else f"{long_rows} rows"
        warnings.append(f"{rows} had more values than the header; the extra values were dropped.")
    return LoadedTable(name, tidy(frame), warnings)


def read_excel(name: str, data: bytes) -> list[LoadedTable]:
    try:
        sheets = pd.read_excel(
            io.BytesIO(data), sheet_name=None, header=None, dtype=str, keep_default_na=False, engine="openpyxl"
        )
    except Exception as exc:
        raise IngestError(f"{name}: could not read the workbook ({exc}).") from exc
    # Sheets without data rows (a notes or cover sheet) are skipped.
    frames = {sheet: frame for sheet, frame in ((s, tidy(f)) for s, f in sheets.items()) if not frame.empty}
    tables = [LoadedTable(name if len(frames) == 1 else f"{name} {sheet}", frame) for sheet, frame in frames.items()]
    if not tables:
        raise IngestError(f"{name}: every sheet is empty.")
    return tables


def decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def sniff_delimiter(text: str) -> str:
    """The delimiter that splits the header line into the most fields; the csv sniffer breaks ties."""
    header = text.lstrip().splitlines()[0]
    counts = {d: header.count(d) for d in CSV_DELIMITERS}
    best = max(counts.values())
    if best == 0:
        return ","
    leaders = [d for d, n in counts.items() if n == best]
    if len(leaders) == 1:
        return leaders[0]
    try:
        return csv.Sniffer().sniff(text[:65536], delimiters="".join(leaders)).delimiter
    except csv.Error:
        return leaders[0]


def tidy(frame: pd.DataFrame) -> pd.DataFrame:
    """Promote the first non-empty row to headers. Blank cells become NULL, empty rows and
    empty unnamed columns are dropped, and headers are made unique."""
    frame = frame.astype(object).map(lambda v: None if pd.isna(v) or v == "" else str(v))
    frame = frame.dropna(how="all")
    if frame.empty:
        return pd.DataFrame()
    headers = list(frame.iloc[0])
    frame = frame.iloc[1:]
    keep = [i for i, h in enumerate(headers) if h is not None or frame.iloc[:, i].notna().any()]
    frame = frame.iloc[:, keep]
    frame.columns = unique_headers([headers[i] for i in keep])
    return frame.reset_index(drop=True)


def unique_headers(headers) -> list[str]:
    # DuckDB column names are case-insensitive, so "Region" and "region" would collide.
    seen: set[str] = set()
    result = []
    for i, header in enumerate(headers, start=1):
        header = "" if header is None or pd.isna(header) else str(header).strip()
        if not header:
            header = f"column_{i}"
        candidate, n = header, 2
        while candidate.lower() in seen:
            candidate = f"{header}_{n}"
            n += 1
        seen.add(candidate.lower())
        result.append(candidate)
    return result


# Google Sheets

SHEET_ID = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
PUBLISHED_ID = re.compile(r"/spreadsheets/d/e/([a-zA-Z0-9_-]+)")


def gsheet_csv_url(link: str) -> str:
    """The CSV export URL for a Google Sheets link. Only docs.google.com is accepted."""
    parsed = urllib.parse.urlparse(link.strip())
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com":
        raise IngestError("Paste a Google Sheets link that starts with https://docs.google.com/spreadsheets/.")
    params = urllib.parse.parse_qs(parsed.query)
    fragment = urllib.parse.parse_qs(parsed.fragment)
    gid = (params.get("gid") or fragment.get("gid") or [None])[0]
    if match := PUBLISHED_ID.search(parsed.path):
        query = {"output": "csv", **({"gid": gid} if gid else {})}
        return f"https://docs.google.com/spreadsheets/d/e/{match.group(1)}/pub?{urllib.parse.urlencode(query)}"
    if match := SHEET_ID.search(parsed.path):
        query = {"format": "csv", **({"gid": gid} if gid else {})}
        return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?{urllib.parse.urlencode(query)}"
    raise IngestError("That link does not look like a Google Sheet.")


def http_get(url: str) -> tuple[bytes, str, str]:
    """Returns (body, content type, filename from Content-Disposition)."""
    request = urllib.request.Request(url, headers={"User-Agent": "dataApp/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
            content_type = response.headers.get_content_type()
            filename = response.headers.get_filename() or ""
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise IngestError(NOT_SHARED) from exc
        raise IngestError(f"Google returned HTTP {exc.code}.") from exc
    except OSError as exc:
        raise IngestError(f"Could not reach Google Sheets ({exc}).") from exc
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise IngestError("The sheet is larger than 50 MB.")
    return body, content_type, filename


NOT_SHARED = "Google did not return the sheet. Set sharing to 'Anyone with the link can view' and try again."


def read_gsheet(link: str, fetch: Callable[[str], tuple[bytes, str, str]] = http_get, name: str = "") -> LoadedTable:
    body, content_type, filename = fetch(gsheet_csv_url(link))
    if content_type == "text/html":
        raise IngestError(NOT_SHARED)  # a sign-in page instead of CSV
    if not name:
        name = re.sub(r"\.csv$", "", filename) or "google_sheet"
    return read_csv(name, body)
