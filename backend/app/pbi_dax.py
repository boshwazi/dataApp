"""DAX to and from the measure catalogue, for Power BI import and export.

Import parses a DAX expression (never runs it) and translates the common patterns into the
catalogue: SUM / AVERAGE / MIN / MAX / COUNT / COUNTROWS / DISTINCTCOUNT, the X iterators over
one table's columns, DIVIDE and arithmetic between measures, and CALCULATE with simple filters
on the measure's own table. Anything else (IF, time intelligence, filters on other tables...)
is reported as not converted, with a reason, and its DAX is kept.

Export writes every catalogue measure as DAX. That direction always works, because the
catalogue is small.
"""

import re
from dataclasses import dataclass

from . import model, model_expr


class Untranslatable(Exception):
    """A DAX expression outside what the catalogue can hold; the message says why, in plain words."""


# Parsing

TOKEN = re.compile(r"""
    \s*(?:
      (?P<str>"(?:[^"]|"")*")
    | (?P<qtable>'(?:[^']|'')+')
    | (?P<bracket>\[[^\]]+\])
    | (?P<num>\d+(?:\.\d+)?)
    | (?P<op><>|>=|<=|&&|\|\||[-+*/=<>(),{}&!])
    | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
    | (?P<comment>//[^\n]*|--[^\n]*|/\*.*?\*/)
    )""", re.VERBOSE | re.DOTALL)


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens, pos = [], 0
    text = text or ""
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        match = TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise Untranslatable(f"It uses syntax this app doesn't read ('{text[pos:pos + 12].strip()}').")
        pos = match.end()
        kind = match.lastgroup
        if kind == "comment":
            continue
        tokens.append((kind, match.group(kind)))
    return tokens


def _unquote_table(token: str) -> str:
    return token[1:-1].replace("''", "'")


def parse(text: str):
    """Nested tuples: ("call", NAME, [args]) ("col", table, column) ("mref", name) ("str", v) ("num", v)
    ("bool", v) ("set", [items]) ("neg", x) ("not", x) (op, left, right)."""
    tokens = tokenize(text)
    pos = 0

    def peek(offset=0):
        return tokens[pos + offset] if pos + offset < len(tokens) else (None, None)

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def expect(value):
        kind, got = take() if pos < len(tokens) else (None, None)
        if got != value:
            raise Untranslatable("The expression is incomplete.")

    def expression():
        return logical()

    def logical():
        node = comparison()
        while peek()[1] in ("&&", "||"):
            node = (take()[1], node, comparison())
        return node

    def comparison():
        node = additive()
        while peek()[1] in ("=", "<>", ">", "<", ">=", "<=") or (peek()[0] == "name" and peek()[1].upper() == "IN"):
            op = take()[1]
            op = "in" if op.upper() == "IN" else op
            node = (op, node, additive())
        return node

    def additive():
        node = term()
        while peek()[1] in ("+", "-", "&"):
            node = (take()[1], node, term())
        return node

    def term():
        node = unary()
        while peek()[1] in ("*", "/"):
            node = (take()[1], node, unary())
        return node

    def unary():
        if peek()[1] == "-":
            take()
            return ("neg", unary())
        if peek()[1] == "!" or (peek()[0] == "name" and peek()[1].upper() == "NOT" and peek(1)[1] != "("):
            take()
            return ("not", unary())
        return primary()

    def primary():
        kind, value = peek()
        if kind is None:
            raise Untranslatable("The expression is incomplete.")
        if value == "(":
            take()
            node = expression()
            expect(")")
            return node
        if value == "{":
            take()
            items = []
            while peek()[1] != "}":
                items.append(expression())
                if peek()[1] == ",":
                    take()
            expect("}")
            return ("set", items)
        if kind == "str":
            take()
            return ("str", value[1:-1].replace('""', '"'))
        if kind == "num":
            take()
            return ("num", float(value))
        if kind == "bracket":
            take()
            return ("mref", value[1:-1].strip())
        if kind == "qtable":
            take()
            if peek()[0] == "bracket":
                return ("col", _unquote_table(value), take()[1][1:-1].strip())
            return ("table", _unquote_table(value))
        if kind == "name":
            take()
            upper = value.upper()
            if peek()[1] == "(":
                take()
                args = []
                while peek()[1] != ")":
                    args.append(expression())
                    if peek()[1] == ",":
                        take()
                    elif peek()[1] != ")":
                        raise Untranslatable("The expression is incomplete.")
                expect(")")
                return ("call", upper, args)
            if upper in ("TRUE", "FALSE"):
                return ("bool", upper == "TRUE")
            if peek()[0] == "bracket":
                return ("col", value, take()[1][1:-1].strip())
            return ("table", value)
        raise Untranslatable(f"Unexpected '{value}'.")

    tree = expression()
    if pos != len(tokens):
        raise Untranslatable(f"Unexpected '{tokens[pos][1]}'.")
    return tree


# DAX → catalogue

AGG_CALLS = {"SUM": "sum", "AVERAGE": "average", "MIN": "min", "MAX": "max", "COUNT": "count", "COUNTA": "count",
             "DISTINCTCOUNT": "count_distinct", "DISTINCTCOUNTNOBLANK": "count_distinct"}
ITERATORS = {"SUMX": "sum", "AVERAGEX": "average", "MINX": "min", "MAXX": "max", "COUNTX": "count"}
COMPARE = {"=": "equals", "==": "equals", "<>": "not_equals", ">": "greater_than", "<": "less_than",
           ">=": "at_least", "<=": "at_most"}
FRIENDLY = {
    "IF": "a condition (IF)", "SWITCH": "a condition (SWITCH)", "TOTALYTD": "time intelligence",
    "DATESYTD": "time intelligence", "SAMEPERIODLASTYEAR": "time intelligence", "DATEADD": "time intelligence",
    "PREVIOUSYEAR": "time intelligence", "PARALLELPERIOD": "time intelligence", "DATESINPERIOD": "time intelligence",
    "ALL": "ALL (removing filters)", "ALLEXCEPT": "ALLEXCEPT (removing filters)", "REMOVEFILTERS": "REMOVEFILTERS",
    "FILTER": "a FILTER table", "RELATED": "RELATED (values from another table)", "USERELATIONSHIP": "USERELATIONSHIP",
    "SELECTEDVALUE": "SELECTEDVALUE (depends on the visual)", "RANKX": "ranking (RANKX)", "VAR": "variables (VAR)",
}


@dataclass
class Context:
    """What the translator knows: table names in the model and measures translated so far."""

    tables: dict[str, list[str]]  # table name → its column names
    measures: dict[str, dict]     # lower-case measure name → catalogue proposal
    helpers: list | None = None   # helper measures made while translating the current one
    current: str = ""


def _why(call: str) -> str:
    return f"It uses {FRIENDLY.get(call, call)}, which datawazi measures can't express yet."


def _table(ctx: Context, name: str) -> str:
    for t in ctx.tables:
        if t.lower() == name.lower():
            return t
    raise Untranslatable(f"It refers to table '{name}', which isn't in the model.")


def _column(ctx: Context, node) -> tuple[str, str]:
    if node[0] != "col":
        raise Untranslatable("It expects a column like 'Table'[Column] here.")
    table = _table(ctx, node[1])
    for c in ctx.tables[table]:
        if c.lower() == node[2].lower():
            return table, c
    raise Untranslatable(f"It refers to column {node[1]}[{node[2]}], which isn't in the model.")


def _is_true(node) -> bool:
    return node == ("bool", True) or (node[0] == "call" and node[1] == "TRUE" and not node[2])


def _logic(node, rec) -> str | None:
    """Conditions in either kind of expression: comparisons, text to compare with, IF, SWITCH, AND, OR, NOT.
    `rec` converts the parts. None when `node` is none of these."""
    kind = node[0]
    if kind == "str":
        return '"' + node[1].replace('"', '""') + '"'
    if kind in COMPARE:
        return f"({rec(node[1])} {'=' if kind == '==' else kind} {rec(node[2])})"
    if kind in ("&&", "||"):
        return f"{'AND' if kind == '&&' else 'OR'}({rec(node[1])}, {rec(node[2])})"
    if kind == "not":
        return f"NOT({rec(node[1])})"
    if kind != "call":
        return None
    name, args = node[1], node[2]
    if name == "IF" and len(args) in (2, 3):
        return f"IF({', '.join(rec(a) for a in args)})"
    if name in ("AND", "OR") and len(args) >= 2:
        return f"{name}({', '.join(rec(a) for a in args)})"
    if name == "NOT" and len(args) == 1:
        return f"NOT({rec(args[0])})"
    if name == "SWITCH" and len(args) >= 3:
        if _is_true(args[0]):
            # SWITCH(TRUE(), cond1, r1, cond2, r2, default) is a chain of IFs.
            pairs, default = args[1:], None
            if len(pairs) % 2:
                default = pairs.pop()
            out = rec(default) if default is not None else None
            for cond, result in reversed(list(zip(pairs[0::2], pairs[1::2]))):
                out = f"IF({rec(cond)}, {rec(result)}{', ' + out if out is not None else ''})"
            return out
        return f"SWITCH({', '.join(rec(a) for a in args)})"
    return None


def _row_expression(ctx: Context, node, table: str) -> str:
    """Arithmetic over one table's columns, in the catalogue's [Column] syntax."""
    kind = node[0]
    if kind == "col":
        t, c = _column(ctx, node)
        if t != table:
            raise Untranslatable(f"It mixes columns from {t} and {table} in one calculation.")
        return f"[{c}]"
    if kind == "num":
        return repr(node[1]).rstrip("0").rstrip(".") if node[1] != int(node[1]) else str(int(node[1]))
    if kind == "neg":
        return f"-({_row_expression(ctx, node[1], table)})"
    if kind in ("+", "-", "*", "/"):
        return f"({_row_expression(ctx, node[1], table)} {kind} {_row_expression(ctx, node[2], table)})"
    logic = _logic(node, lambda n: _row_expression(ctx, n, table))
    if logic is not None:
        return logic
    if kind == "call":
        raise Untranslatable(_why(node[1]))
    raise Untranslatable("Its row calculation is more than arithmetic over columns.")


def _strip(expression: str) -> str:
    """Drop one pair of outer brackets the translator added."""
    if expression.startswith("(") and expression.endswith(")"):
        try:
            model_expr.parse(expression[1:-1])
            depth = 0
            for i, ch in enumerate(expression):
                depth += ch == "("
                depth -= ch == ")"
                if depth == 0 and i < len(expression) - 1:
                    return expression
            return expression[1:-1]
        except model_expr.ExprError:
            return expression
    return expression


def _filter(ctx: Context, node, table: str) -> dict:
    kind = node[0]
    if kind == "call" and node[1] == "KEEPFILTERS" and len(node[2]) == 1:
        return _filter(ctx, node[2][0], table)
    if kind == "call" and node[1] == "ISBLANK" and len(node[2]) == 1:
        t, c = _column(ctx, node[2][0])
        return _same_table({"column": c, "operator": "is_blank"}, t, table)
    if kind in ("not", "call") and (kind == "not" or node[1] == "NOT"):
        inner = node[1] if kind == "not" else (node[2][0] if len(node[2]) == 1 else None)
        if inner and inner[0] == "call" and inner[1] == "ISBLANK":
            t, c = _column(ctx, inner[2][0])
            return _same_table({"column": c, "operator": "is_not_blank"}, t, table)
    if kind in COMPARE or kind == "in":
        left, right = node[1], node[2]
        if left[0] != "col" and right[0] == "col" and kind in COMPARE:
            left, right = right, left
            kind = {">": "<", "<": ">", ">=": "<=", "<=": ">="}.get(kind, kind)
        t, c = _column(ctx, left)
        if kind == "in":
            if right[0] != "set" or not all(i[0] in ("str", "num", "bool") for i in right[1]):
                raise Untranslatable("Its IN filter isn't a fixed list of values.")
            return _same_table({"column": c, "operator": "in", "values": [_constant(i) for i in right[1]]}, t, table)
        if right[0] not in ("str", "num", "bool"):
            raise Untranslatable("Its filter compares with something other than a fixed value.")
        return _same_table({"column": c, "operator": COMPARE[kind], "value": _constant(right)}, t, table)
    if kind == "call" and node[1] in FRIENDLY:
        raise Untranslatable(_why(node[1]))
    raise Untranslatable("Its CALCULATE filter is more complex than column = value.")


def _constant(node):
    value = node[1]
    return int(value) if node[0] == "num" and value == int(value) else value


def _same_table(item: dict, filter_table: str, table: str) -> dict:
    """A filter on the measure's own table, or on a related one (named, for the importer to map)."""
    return item if filter_table == table else {**item, "table": filter_table}


def _aggregate(ctx: Context, node) -> dict | None:
    """A node that is one aggregation over one table, as a catalogue proposal (None if it isn't one)."""
    kind = node[0]
    if kind == "mref":
        found = ctx.measures.get(node[1].lower())
        if found and found["kind"] == "aggregate":
            return {k: v for k, v in found.items() if k in ("kind", "table", "aggregation", "expression", "filters")}
        return None
    if kind != "call":
        return None
    name, args = node[1], node[2]
    if name in AGG_CALLS and len(args) == 1:
        table, column = _column(ctx, args[0])
        return {"kind": "aggregate", "table": table, "aggregation": AGG_CALLS[name], "expression": f"[{column}]", "filters": []}
    if name == "COUNTROWS" and len(args) == 1 and args[0][0] == "table":
        return {"kind": "aggregate", "table": _table(ctx, args[0][1]), "aggregation": "count", "expression": None, "filters": []}
    if name in ITERATORS and len(args) == 2 and args[0][0] == "table":
        table = _table(ctx, args[0][1])
        return {"kind": "aggregate", "table": table, "aggregation": ITERATORS[name],
                "expression": _strip(_row_expression(ctx, args[1], table)), "filters": []}
    if name == "CALCULATE" and args:
        inner = _aggregate(ctx, args[0])
        if inner is None:
            raise Untranslatable("Its CALCULATE wraps something other than a single aggregation.")
        filters = list(inner["filters"]) + [_filter(ctx, f, inner["table"]) for f in args[1:]]
        return {**inner, "filters": filters}
    return None


def _formula(ctx: Context, node) -> str:
    """Arithmetic between measures, in the catalogue's [Measure] syntax."""
    kind = node[0]
    if kind == "mref":
        if node[1].lower() not in ctx.measures:
            raise Untranslatable(f"It uses [{node[1]}], which wasn't converted.")
        return f"[{ctx.measures[node[1].lower()]['name']}]"
    if kind == "num":
        return _row_expression(ctx, node, "")
    if kind == "neg":
        return f"-({_formula(ctx, node[1])})"
    if kind in ("+", "-", "*", "/"):
        return f"({_formula(ctx, node[1])} {kind} {_formula(ctx, node[2])})"
    if kind == "call" and node[1] == "IF" and any(a[0] == "str" for a in node[2][1:]):
        raise Untranslatable(f"It uses {FRIENDLY['IF']} that returns text; datawazi measures are numbers.")
    if (logic := _logic(node, lambda n: _formula(ctx, n))) is not None:
        return logic
    if kind == "call" and node[1] == "DIVIDE" and len(node[2]) in (2, 3):
        if len(node[2]) == 3 and node[2][2] not in (("num", 0.0),):
            raise Untranslatable("Its DIVIDE has an alternative result, which datawazi measures can't express yet.")
        return f"({_formula(ctx, node[2][0])} / {_formula(ctx, node[2][1])})"
    if kind == "call" and (timed := _time(ctx, node)) is not None:
        # Time intelligence inside arithmetic, e.g. [Revenue] - CALCULATE([Revenue], SAMEPERIODLASTYEAR(...)).
        if ctx.helpers is None:
            raise Untranslatable("It mixes time intelligence with other measures; split it into two measures first.")
        number = len(ctx.helpers) + 1
        base = _time_base(ctx, timed[2], f"{ctx.current} (part {number})")
        helper = {"name": f"{ctx.current} (part {len(ctx.helpers) + 1})", "kind": "time", "base": base,
                  "calc": timed[0], "months": timed[1],
                  "description": f"Part of {ctx.current}, split out when it was imported from Power BI."}
        ctx.helpers.append(helper)
        return f"[{helper['name']}]"
    if kind == "call" and (agg := _aggregate(ctx, node)) is not None:
        # An aggregation inside arithmetic becomes a helper measure, as you would split it by hand.
        if ctx.helpers is None:
            raise Untranslatable("It mixes an aggregation with other measures; split it into two measures first.")
        helper = {"name": f"{ctx.current} (part {len(ctx.helpers) + 1})", **agg,
                  "description": f"Part of {ctx.current}, split out when it was imported from Power BI."}
        ctx.helpers.append(helper)
        return f"[{helper['name']}]"
    if kind == "call":
        raise Untranslatable(_why(node[1]))
    raise Untranslatable("It isn't arithmetic between measures.")


# Time intelligence: the usual DAX patterns, as datawazi time measures.
TO_DATE = {"TOTALYTD": "ytd", "TOTALQTD": "qtd", "TOTALMTD": "mtd", "DATESYTD": "ytd", "DATESQTD": "qtd", "DATESMTD": "mtd"}


def _date_column(node) -> bool:
    return node[0] == "col" or (node[0] == "call" and node[1] in ("LASTDATE", "MAX") and node[2] and node[2][0][0] == "col")


def _time_filter(node) -> tuple[str, int | None] | None:
    """The time calculation one CALCULATE filter stands for, or None."""
    if node[0] != "call":
        return None
    name, args = node[1], node[2]
    if name == "SAMEPERIODLASTYEAR" and len(args) == 1:
        inner = args[0]
        if _date_column(inner):
            return ("prior_year", None)
        if inner[0] == "call" and inner[1] == "DATESYTD" and len(inner[2]) == 1:
            return ("prior_year_ytd", None)
    if name == "DATEADD" and len(args) == 3 and _date_column(args[0]) and args[2][0] in ("col", "table"):
        unit = (args[2][1] if args[2][0] == "table" else args[2][2]).upper()
        step = args[1]
        n = step[1][1] if step[0] == "neg" and step[1][0] == "num" else None  # how far back
        if (unit, n) in (("YEAR", 1), ("MONTH", 12), ("QUARTER", 4)):
            return ("prior_year", None)
    if name in ("DATESYTD", "DATESQTD", "DATESMTD") and len(args) >= 1:
        inner = args[0]
        if _date_column(inner):
            return (TO_DATE[name], None)
        if name == "DATESYTD" and inner[0] == "call" and inner[1] == "SAMEPERIODLASTYEAR":
            return ("prior_year_ytd", None)
    if name == "DATESINPERIOD" and len(args) == 4 and _date_column(args[0]) and _date_column(args[1]):
        step, unit = args[2], args[3]
        months = step[1][1] if step[0] == "neg" and step[1][0] == "num" else None  # -3 months back
        unit_name = (unit[1] if unit[0] == "table" else "").upper()
        if months and unit_name == "MONTH" and float(months).is_integer():
            return ("rolling", int(months))
    return None


def _time(ctx: Context, node) -> tuple[str, int | None, object] | None:
    """(calc, months, base node) when `node` is time intelligence over one measure or aggregation."""
    if node[0] != "call":
        return None
    name, args = node[1], node[2]
    if name in ("TOTALYTD", "TOTALQTD", "TOTALMTD") and len(args) >= 2 and _date_column(args[1]):
        return (TO_DATE[name], None, args[0])
    if name == "CALCULATE" and len(args) == 2:
        found = _time_filter(args[1])
        if found:
            return (*found, args[0])
    return None


def _time_base(ctx: Context, node, name: str) -> str:
    """The base measure's name: an existing measure, or a helper made from the aggregation inside."""
    if node[0] == "mref":
        if node[1].lower() not in ctx.measures:
            raise Untranslatable(f"It uses [{node[1]}], which wasn't converted.")
        return ctx.measures[node[1].lower()]["name"]
    agg = _aggregate(ctx, node)
    if agg is None or ctx.helpers is None:
        raise Untranslatable("Its time intelligence wraps something other than a measure or one aggregation.")
    helper = {"name": f"{name} (base)", **agg, "description": f"The amount {name} works out over time, split out on import."}
    ctx.helpers.append(helper)
    return helper["name"]


def to_measure(name: str, dax: str, ctx: Context) -> tuple[dict, list[dict]]:
    """A catalogue proposal for one DAX measure, plus any helper measures it was split into."""
    if re.search(r"\bVAR\b", dax or "", re.IGNORECASE):
        raise Untranslatable(_why("VAR"))
    tree = parse(dax)
    timed = _time(ctx, tree)
    if timed is not None:
        ctx.helpers, ctx.current = [], name
        try:
            base = _time_base(ctx, timed[2], name)
            return {"name": name, "kind": "time", "base": base, "calc": timed[0], "months": timed[1]}, ctx.helpers
        finally:
            ctx.helpers, ctx.current = None, ""
    agg = _aggregate(ctx, tree)
    if agg is not None:
        return {"name": name, **agg}, []
    ctx.helpers, ctx.current = [], name
    try:
        expression = _strip(_formula(ctx, tree))
        return {"name": name, "kind": "formula", "expression": expression}, ctx.helpers
    finally:
        ctx.helpers, ctx.current = None, ""


def format_from(format_string: str | None) -> dict:
    """A Power BI format string as a catalogue format."""
    text = (format_string or "").strip()
    if not text:
        return {"type": "number", "decimals": 0}
    first = text.split(";")[0]
    decimals = len(first.split(".")[1].rstrip("%").replace(",", "")) if "." in first else 0
    decimals = min(decimals, 4)
    if "%" in first:
        return {"type": "percent", "decimals": decimals}
    symbol = re.search(r'"?(RM|\$|€|£|S\$)"?', first)
    if symbol:
        return {"type": "currency", "decimals": decimals, "currency": symbol.group(1)}
    return {"type": "integer" if first in ("0", "#,0") else "number", "decimals": decimals}


# Catalogue → DAX


def quote_table(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def column(table: str, name: str) -> str:
    return f"{quote_table(table)}[{name.replace(']', ']]')}]"


def _value(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        y, m, d = text.split("-")
        return f"DATE({int(y)}, {int(m)}, {int(d)})"
    return '"' + text.replace('"', '""') + '"'


def _expr_dax(tree, ref) -> str:
    """DAX for an expression tree; `ref` writes a reference (a column, or a measure)."""
    kind = tree[0]
    if kind == "ref":
        return ref(tree[1])
    if kind == "num":
        return repr(tree[1]) if tree[1] != int(tree[1]) else str(int(tree[1]))
    if kind == "str":
        return '"' + tree[1].replace('"', '""') + '"'
    if kind == "neg":
        return f"-({_expr_dax(tree[1], ref)})"
    if kind == "cmp":
        return f"({_expr_dax(tree[2], ref)} {tree[1]} {_expr_dax(tree[3], ref)})"
    if kind == "if":
        parts = [tree[1], tree[2]] + ([tree[3]] if tree[3] is not None else [])
        return f"IF({', '.join(_expr_dax(p, ref) for p in parts)})"
    if kind == "switch":
        parts = [tree[1]] + [x for pair in tree[2] for x in pair] + ([tree[3]] if tree[3] is not None else [])
        return f"SWITCH({', '.join(_expr_dax(p, ref) for p in parts)})"
    if kind in ("and", "or"):
        return "(" + (" && " if kind == "and" else " || ").join(_expr_dax(x, ref) for x in tree[1]) + ")"
    if kind == "not":
        return f"NOT({_expr_dax(tree[1], ref)})"
    left, right = _expr_dax(tree[1], ref), _expr_dax(tree[2], ref)
    if kind == "/":
        return f"DIVIDE({left}, {right})"
    return f"({left} {kind} {right})"


def _row_dax(tree, table: str) -> str:
    return _expr_dax(tree, lambda name: column(table, name))


def _formula_dax(tree) -> str:
    return _expr_dax(tree, lambda name: "[" + name.replace("]", "]]") + "]")


TIME_DAX = {
    "ytd": "TOTALYTD({m}, {d})",
    "qtd": "TOTALQTD({m}, {d})",
    "mtd": "TOTALMTD({m}, {d})",
    "prior_year": "CALCULATE({m}, SAMEPERIODLASTYEAR({d}))",
    "prior_year_ytd": "CALCULATE({m}, DATESYTD(SAMEPERIODLASTYEAR({d})))",
    "rolling": "CALCULATE({m}, DATESINPERIOD({d}, MAX({d}), -{n}, MONTH))",
}


def from_measure(measure: dict, table_name: str | None, names: dict[str, str] | None = None) -> str:
    """DAX for a catalogue measure. `table_name` is the aggregate's table as Power BI will call it;
    `names` maps table ids to Power BI names, for filters on related tables."""
    if measure["kind"] == "formula":
        return _formula_dax(model_expr.parse(measure["expression"]))
    if measure["kind"] == "time":
        base = "[" + measure["base"].replace("]", "]]") + "]"
        return TIME_DAX[measure["calc"]].format(m=base, d=column(model.DATE_NAME, "Date"), n=measure.get("months") or 3)
    tree = model_expr.parse(measure["expression"]) if measure.get("expression") else None
    aggregation = measure["aggregation"]
    single = tree is not None and tree[0] == "ref"
    if tree is None:
        inner = f"COUNTROWS({quote_table(table_name)})"
    elif single:
        col = column(table_name, tree[1])
        inner = {"sum": f"SUM({col})", "average": f"AVERAGE({col})", "min": f"MIN({col})", "max": f"MAX({col})",
                 "count": f"COUNTA({col})", "count_distinct": f"DISTINCTCOUNTNOBLANK({col})"}[aggregation]
    else:
        row = _row_dax(tree, table_name)
        if aggregation == "count_distinct":
            inner = f"COUNTROWS(FILTER(DISTINCT(SELECTCOLUMNS({quote_table(table_name)}, \"v\", {row})), NOT ISBLANK([v])))"
        else:
            fn = {"sum": "SUMX", "average": "AVERAGEX", "min": "MINX", "max": "MAXX", "count": "COUNTX"}[aggregation]
            inner = f"{fn}({quote_table(table_name)}, {row})"
    filters = []
    for item in measure.get("filters") or []:
        col = column((names or {}).get(item["table"], item["table"]) if item.get("table") else table_name, item["column"])
        op = item["operator"]
        value = item.get("values", item.get("value")) if op in ("in", "not_in") else item.get("value")
        if op == "is_blank":
            filters.append(f"KEEPFILTERS(ISBLANK({col}))")
        elif op == "is_not_blank":
            filters.append(f"KEEPFILTERS(NOT ISBLANK({col}))")
        elif op in ("in", "not_in"):
            values = ", ".join(_value(v) for v in (value if isinstance(value, list) else [value]))
            expr = f"{col} IN {{{values}}}"
            filters.append(f"KEEPFILTERS({'NOT ' if op == 'not_in' else ''}{expr})")
        else:
            symbol = {"equals": "=", "not_equals": "<>", "greater_than": ">", "less_than": "<",
                      "at_least": ">=", "at_most": "<="}[op]
            filters.append(f"KEEPFILTERS({col} {symbol} {_value(value)})")
    return f"CALCULATE({inner}, {', '.join(filters)})" if filters else inner


def format_string(fmt: dict) -> str:
    decimals = int(fmt.get("decimals", 0) or 0)
    tail = ("." + "0" * decimals) if decimals else ""
    kind = fmt.get("type", "number")
    if kind == "percent":
        return f"0{tail}%"
    if kind == "currency":
        return f'"{fmt.get("currency", "RM")} "#,##0{tail}'
    if kind == "integer":
        return "#,##0"
    return f"#,##0{tail}"
