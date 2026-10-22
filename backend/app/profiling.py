"""Column profiles for raw text tables, computed in DuckDB.

A profile is what the AI sees instead of the data: counts, the type the values look
like, ranges, the most common values, and the issues a cleaning step would fix.

Raw columns are text, so their type is detected from the values. Typed columns (Transform output)
already have a type, so only their counts, range and common values are worked out. Date formats are
only tried on values shaped like dates: trying dozens of formats on every value was most of the time
spent on large tables.
"""

from datetime import UTC, datetime

import duckdb

# "Unknown" and "missing" are left out: they are usually deliberate labels (Transform fills blanks with them).
PLACEHOLDERS = ("n/a", "na", "null", "none", "nan", "-", "--", "?", "#n/a")
DAY_FIRST = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"]
MONTH_FIRST = ["%m/%d/%Y", "%m-%d-%Y"]
WORDY_DATES = ["%Y/%m/%d", "%d %b %Y", "%b %d, %Y", "%d-%b-%Y", "%d %B %Y", "%B %d, %Y"]
TIMES = ["", " %H:%M", " %H:%M:%S", " %I:%M %p", " %I:%M:%S %p"]


def with_times(formats: list[str]) -> list[str]:
    """Each date format, also followed by a time of day (6/28/2016 23:34, 3/5/2016 1:05 PM)."""
    return [f + t for f in formats for t in TIMES]


DATE_FORMATS = with_times(["%Y-%m-%d"] + DAY_FIRST + MONTH_FIRST + WORDY_DATES)
BOOLEANS = ("true", "false", "yes", "no", "y", "n", "t", "f")
TRUE_WORDS = ("true", "yes", "y", "t")
# Currency prefixes, thousands separators and spaces, stripped before reading a number.
NUMBER_NOISE = r"^(RM|MYR|USD|\$|€|£)\s*|,|\s"
# Values worth trying the date formats on: 2024-01-05, 5/1/2024, 05.01.2024, 5 Jan 2024, January 5, 2024.
DATE_SHAPED = r"^(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}[ -][A-Za-z]{3,9}[ -]\d{4}|[A-Za-z]{3,9} \d{1,2}, \d{4})"
TYPED = {"BIGINT": "integer", "INTEGER": "integer", "HUGEINT": "integer", "SMALLINT": "integer", "TINYINT": "integer",
         "UBIGINT": "integer", "UINTEGER": "integer", "DOUBLE": "decimal", "FLOAT": "decimal", "REAL": "decimal",
         "DATE": "date", "TIMESTAMP": "date", "TIMESTAMP WITH TIME ZONE": "date", "BOOLEAN": "boolean"}
TOP_VALUES = 5
SAMPLE_ROWS = 1_000_000  # above this, a text column's type checks run on a sample of this many rows
# A column counts as a type when at least this share of its filled values parse as it.
MOSTLY = 0.8


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sql_list(values) -> str:
    return "(" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


def profile_table(con: duckdb.DuckDBPyConnection, table: str, schema: str | None = None) -> dict:
    t = f"{quote(schema)}.{quote(table)}" if schema else quote(table)
    rows = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    distinct_rows = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT * FROM {t})").fetchone()[0]
    columns = [(row[0], row[1]) for row in con.execute(f"DESCRIBE {t}").fetchall()]
    return {
        "rows": rows,
        "duplicate_rows": rows - distinct_rows,
        "columns": [profile_typed(con, t, name, kind, rows) if typed_kind(kind) else profile_column(con, t, name, rows)
                    for name, kind in columns],
        "profiled_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def typed_kind(sql_type: str) -> str | None:
    """The logical type of a typed column (DECIMAL(18,2) is a decimal), or None for text."""
    base = sql_type.upper().split("(")[0].strip()
    return "decimal" if base == "DECIMAL" else TYPED.get(base)


def date_value(v: str) -> str:
    return (f"CASE WHEN regexp_matches({v}, '{DATE_SHAPED}') "
            f"THEN coalesce(TRY_CAST({v} AS TIMESTAMP), try_strptime({v}, {DATE_FORMATS})) END")


def top_values(con, t: str, name: str) -> list[dict]:
    return [{"value": value, "count": count} for value, count in con.execute(
        f"SELECT CAST({quote(name)} AS VARCHAR), count(*) FROM {t} WHERE {quote(name)} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {TOP_VALUES}").fetchall()]


def profile_typed(con: duckdb.DuckDBPyConnection, t: str, name: str, sql_type: str, rows: int) -> dict:
    """A column that already has a type: no guessing, no text checks."""
    detected = typed_kind(sql_type)
    c = quote(name)
    numeric = detected in ("integer", "decimal")
    present, distinct, low, high, mean, min_length, max_length = con.execute(
        f"SELECT count({c}), count(DISTINCT {c}), min({c}), max({c}), "
        f"{f'avg({c})' if numeric else 'NULL'}, min(length(CAST({c} AS VARCHAR))), max(length(CAST({c} AS VARCHAR))) "
        f"FROM {t}").fetchone()
    if detected == "date":
        low, high = (x.isoformat() if x is not None else None for x in (low, high))
    elif detected == "decimal":
        low, high = (float(x) if x is not None else None for x in (low, high))
    elif detected == "boolean":
        low = high = None
    blanks = rows - present
    return {
        "name": name, "detected_type": detected, "type_confidence": 1.0, "filled": present, "blanks": blanks,
        "placeholders": 0, "distinct": distinct, "unique": rows > 0 and distinct == rows,
        "min_length": min_length, "max_length": max_length, "min": low, "max": high,
        "mean": round(float(mean), 4) if mean is not None else None,
        "top_values": top_values(con, t, name),
        "issues": issues(rows, blanks, 0, 0, distinct, distinct, detected, 1.0, present, 0, 0, 1),
    }


def profile_column(con: duckdb.DuckDBPyConnection, t: str, name: str, rows: int) -> dict:
    """A text column: its counts, and the type its values look like.

    Over SAMPLE_ROWS rows, the type checks (casts and patterns on every value) run on a fixed sample and
    are scaled up; counts, blanks, distinct values and lengths are always exact."""
    text = f"CAST({quote(name)} AS VARCHAR)"
    v = "trim(v)"
    cleaned_number = f"TRY_CAST(regexp_replace({v}, '{NUMBER_NOISE}', '', 'gi') AS DOUBLE)"
    placeholder = f"(lower({v}) IN {sql_list(PLACEHOLDERS)} OR {v} = '')"
    type_checks = f"""
            count(TRY_CAST({v} AS BIGINT)) AS integers,
            count(TRY_CAST({v} AS DOUBLE)) FILTER (WHERE lower({v}) NOT IN ('nan', 'inf', '-inf', 'infinity')) AS numbers,
            count({cleaned_number}) AS symbol_numbers,
            count(*) FILTER (WHERE lower({v}) IN {sql_list(BOOLEANS)}) AS booleans,
            count({date_value(v)}) AS dates,
            count(DISTINCT regexp_replace(regexp_replace({v}, '[0-9]', '9', 'g'), '[A-Za-z]+', 'a', 'g')) AS shapes"""
    sampled = rows > SAMPLE_ROWS
    present, placeholders, distinct, distinct_folded, untrimmed, min_length, max_length, *checks = con.execute(
        f"""
        SELECT
            count(v) AS present,
            count(*) FILTER (WHERE {placeholder}) AS placeholders,
            count(DISTINCT v) AS distinct_values,
            count(DISTINCT lower({v})) AS distinct_folded,
            count(*) FILTER (WHERE v <> {v}) AS untrimmed,
            min(length(v)) AS min_length,
            max(length(v)) AS max_length{"" if sampled else "," + type_checks}
        FROM (SELECT {text} AS v FROM {t})
        """
    ).fetchone()
    filled = present - placeholders
    if sampled:
        sample_present, sample_placeholders, *checks = con.execute(
            f"SELECT count(v), count(*) FILTER (WHERE {placeholder}), {type_checks} "
            # Row-by-row (bernoulli) sampling is parallel; a reservoir of exact size was slower than reading
            # everything, and block (system) sampling is too coarse on smaller tables.
            f"FROM (SELECT {text} AS v FROM {t} USING SAMPLE {100 * SAMPLE_ROWS / rows:.4f}% (bernoulli, 7))"
        ).fetchone()
        sample_filled = sample_present - sample_placeholders
        *counts, shapes = checks

        def scaled(count):
            # All of the sample passing means all of the column is taken to pass: exact where it matters most.
            if not sample_filled:
                return 0
            return filled if count >= sample_filled else round(count * filled / sample_filled)
        integers, numbers, symbol_numbers, booleans, dates = (scaled(c) for c in counts)
    else:
        integers, numbers, symbol_numbers, booleans, dates, shapes = checks
    blanks = rows - present
    detected, confidence = detect_type(filled, integers, numbers, symbol_numbers, booleans, dates)

    profile = {
        "name": name,
        "detected_type": detected,
        "type_confidence": confidence,
        "filled": filled,
        "blanks": blanks,
        "placeholders": placeholders,
        "distinct": distinct,
        "unique": rows > 0 and distinct == rows,
        "min_length": min_length,
        "max_length": max_length,
        "min": None,
        "max": None,
        "mean": None,
        "top_values": top_values(con, t, name),
    }
    if detected in ("integer", "decimal"):
        low, high, mean = con.execute(
            f"SELECT min(n), max(n), avg(n) FROM (SELECT {cleaned_number} AS n FROM (SELECT {text} AS v FROM {t}))"
        ).fetchone()
        if detected == "integer" and low is not None:
            low, high = int(low), int(high)
        profile.update(min=low, max=high, mean=round(mean, 4) if mean is not None else None)
    elif detected == "date":
        low, high = con.execute(
            f"SELECT min(d), max(d) FROM (SELECT {date_value(v)} AS d FROM (SELECT {text} AS v FROM {t}))"
        ).fetchone()
        profile.update(min=low.isoformat() if low else None, max=high.isoformat() if high else None)

    profile["issues"] = issues(
        rows, blanks, placeholders, untrimmed, distinct, distinct_folded, detected, confidence,
        filled, numbers, symbol_numbers, shapes, approximate=sampled,
    )
    if sampled:
        profile["sampled"] = SAMPLE_ROWS
    return profile


def detect_type(filled, integers, numbers, symbol_numbers, booleans, dates) -> tuple[str, float]:
    if filled == 0:
        return "empty", 1.0
    # Order matters: "1" parses as an integer and a date-free number, "2024-01-05" only as a date.
    candidates = [
        ("boolean", booleans),
        ("integer", integers),
        ("decimal", max(numbers, symbol_numbers)),
        ("date", dates),
    ]
    for kind, count in candidates:
        if count >= filled:
            return kind, 1.0
    kind, count = max(candidates, key=lambda c: c[1])
    share = count / filled
    if share >= MOSTLY:
        return kind, round(share, 3)
    return "text", 1.0


def issues(rows, blanks, placeholders, untrimmed, distinct, distinct_folded, detected, confidence,
           filled, numbers, symbol_numbers, shapes, approximate: bool = False) -> list[str]:
    found = []
    about = "about " if approximate else ""
    if rows and blanks:
        found.append(f"{blanks} blank ({blanks / rows:.0%})")
    if placeholders:
        found.append(f"{placeholders} placeholder values such as 'N/A' that mean blank")
    if untrimmed:
        found.append(f"{untrimmed} values with leading or trailing spaces")
    if confidence < 1:
        found.append(f"mixed types: {confidence:.0%} of values look like {detected}")
    if detected == "decimal" and symbol_numbers > numbers:
        found.append(f"{about}{symbol_numbers - numbers:,} numbers stored with currency symbols or thousands separators")
    if detected == "date" and shapes > 1:
        found.append(f"dates written in {about}{shapes} different formats")
    if detected == "text" and distinct_folded < distinct:
        found.append(f"{distinct - distinct_folded} values differ only by case or spacing")
    return found
