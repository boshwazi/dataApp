"""Column profiles for raw text tables, computed in DuckDB.

A profile is what the AI sees instead of the data: counts, the type the values look
like, ranges, the most common values, and the issues a cleaning step would fix.
"""

from datetime import datetime, timezone

import duckdb

PLACEHOLDERS = ("n/a", "na", "null", "none", "nan", "-", "--", "?", "#n/a", "missing", "unknown")
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y", "%d-%b-%Y"]
BOOLEANS = ("true", "false", "yes", "no", "y", "n", "t", "f")
TOP_VALUES = 5
# A column counts as a type when at least this share of its filled values parse as it.
MOSTLY = 0.8


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sql_list(values) -> str:
    return "(" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


def profile_table(con: duckdb.DuckDBPyConnection, table: str) -> dict:
    t = quote(table)
    rows = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    distinct_rows = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT * FROM {t})").fetchone()[0]
    columns = [row[0] for row in con.execute(f"DESCRIBE {t}").fetchall()]
    return {
        "rows": rows,
        "duplicate_rows": rows - distinct_rows,
        "columns": [profile_column(con, t, name, rows) for name in columns],
        "profiled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def profile_column(con: duckdb.DuckDBPyConnection, t: str, name: str, rows: int) -> dict:
    v = "trim(v)"
    cleaned_number = f"TRY_CAST(regexp_replace({v}, '^(RM|MYR|USD|\\$|€|£)\\s*|,|\\s', '', 'gi') AS DOUBLE)"
    stats = con.execute(
        f"""
        SELECT
            count(v) AS present,
            count(*) FILTER (WHERE lower({v}) IN {sql_list(PLACEHOLDERS)} OR {v} = '') AS placeholders,
            count(DISTINCT v) AS distinct_values,
            count(DISTINCT lower({v})) AS distinct_folded,
            count(*) FILTER (WHERE v <> {v}) AS untrimmed,
            count(TRY_CAST({v} AS BIGINT)) AS integers,
            count(TRY_CAST({v} AS DOUBLE)) FILTER (WHERE lower({v}) NOT IN ('nan', 'inf', '-inf', 'infinity')) AS numbers,
            count({cleaned_number}) AS symbol_numbers,
            count(*) FILTER (WHERE lower({v}) IN {sql_list(BOOLEANS)}) AS booleans,
            count(coalesce(TRY_CAST({v} AS TIMESTAMP), try_strptime({v}, {DATE_FORMATS}))) AS dates,
            count(DISTINCT regexp_replace(regexp_replace({v}, '[0-9]', '9', 'g'), '[A-Za-z]+', 'a', 'g')) AS shapes,
            min(length(v)) AS min_length,
            max(length(v)) AS max_length
        FROM (SELECT {quote(name)} AS v FROM {t})
        """
    ).fetchone()
    (present, placeholders, distinct, distinct_folded, untrimmed, integers, numbers, symbol_numbers,
     booleans, dates, shapes, min_length, max_length) = stats
    filled = present - placeholders
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
        "top_values": [
            {"value": value, "count": count}
            for value, count in con.execute(
                f"SELECT {quote(name)}, count(*) FROM {t} WHERE {quote(name)} IS NOT NULL "
                f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {TOP_VALUES}"
            ).fetchall()
        ],
    }
    if detected in ("integer", "decimal"):
        low, high, mean = con.execute(
            f"SELECT min(n), max(n), avg(n) FROM (SELECT {cleaned_number} AS n FROM (SELECT {quote(name)} AS v FROM {t}))"
        ).fetchone()
        if detected == "integer" and low is not None:
            low, high = int(low), int(high)
        profile.update(min=low, max=high, mean=round(mean, 4) if mean is not None else None)
    elif detected == "date":
        low, high = con.execute(
            f"SELECT min(d), max(d) FROM (SELECT coalesce(TRY_CAST({v} AS TIMESTAMP), try_strptime({v}, {DATE_FORMATS})) AS d "
            f"FROM (SELECT {quote(name)} AS v FROM {t}))"
        ).fetchone()
        profile.update(min=low.isoformat() if low else None, max=high.isoformat() if high else None)

    profile["issues"] = issues(
        rows, blanks, placeholders, untrimmed, distinct, distinct_folded, detected, confidence,
        filled, numbers, symbol_numbers, shapes,
    )
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
           filled, numbers, symbol_numbers, shapes) -> list[str]:
    found = []
    if rows and blanks:
        found.append(f"{blanks} blank ({blanks / rows:.0%})")
    if placeholders:
        found.append(f"{placeholders} placeholder values such as 'N/A' that mean blank")
    if untrimmed:
        found.append(f"{untrimmed} values with leading or trailing spaces")
    if confidence < 1:
        found.append(f"mixed types: {confidence:.0%} of values look like {detected}")
    if detected == "decimal" and symbol_numbers > numbers:
        found.append(f"{symbol_numbers - numbers} numbers stored with currency symbols or thousands separators")
    if detected == "date" and shapes > 1:
        found.append(f"dates written in {shapes} different formats")
    if detected == "text" and distinct_folded < distinct:
        found.append(f"{distinct - distinct_folded} values differ only by case or spacing")
    return found
