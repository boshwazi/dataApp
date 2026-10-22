"""A small language for measures and calculated columns.

  [Qty] * [Unit Price]                          a row expression over one table's columns
  [Revenue] / [Orders]                          a formula over other measures
  IF([Qty] > 10, [Qty] * [Price], 0)            conditions, with = <> < > <= >=
  SWITCH([Region], "North", 1, "South", 2, 0)   pick by value; text only inside comparisons
  AND(a, b), OR(a, b), NOT(a)

It is parsed into a tree and compiled to SQL (or evaluated in Python) by this module,
so the AI never writes SQL. Division by zero gives a blank, as in DAX's DIVIDE.
Results are numbers: text appears only as something to compare with.
"""

import re

TOKEN = re.compile(
    r"""\s*(?:
      (?P<ref>\[[^\]]+\])
    | (?P<num>\d+(?:\.\d+)?)
    | (?P<str>"(?:[^"]|"")*")
    | (?P<name>[A-Za-z_][A-Za-z_0-9]*)
    | (?P<sym><=|>=|<>|[-+*/(),=<>])
    | (?P<bad>\S)
    )""",
    re.VERBOSE,
)
COMPARE = ("=", "<>", "<", ">", "<=", ">=")
FUNCTIONS = ("IF", "SWITCH", "AND", "OR", "NOT")


class ExprError(ValueError):
    pass


def tokenize(text: str) -> list[tuple[str, str]]:
    if not isinstance(text, str) or not text.strip():
        raise ExprError("The expression is empty.")
    tokens = []
    for match in TOKEN.finditer(text.strip()):
        kind = match.lastgroup
        value = match.group(kind)
        if kind == "bad":
            raise ExprError(f"Unexpected '{value}'. Use [Names], numbers, + - * /, comparisons, IF, SWITCH and brackets.")
        if kind == "ref":
            tokens.append(("ref", value[1:-1].strip()))
        elif kind == "str":
            tokens.append(("str", value[1:-1].replace('""', '"')))
        elif kind == "name":
            tokens.append(("name", value.upper()))
        else:
            tokens.append((kind, value))
    return tokens


def parse(text: str):
    """Parse into nested tuples: ("ref", name) | ("num", value) | ("str", text) | ("neg", x) | (op, left, right)
    | ("cmp", op, left, right) | ("if", cond, then, else) | ("switch", subject, [(value, result)], default)
    | ("and", [x...]) | ("or", [x...]) | ("not", x)."""
    tokens = tokenize(text)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def take_if(token):
        return take() if peek() == token else None

    def expression():
        node = additive()
        if peek() and peek()[0] == "sym" and peek()[1] in COMPARE:
            op = take()[1]
            node = ("cmp", op, node, additive())
        return node

    def additive():
        node = term()
        while peek() in (("sym", "+"), ("sym", "-")):
            node = (take()[1], node, term())
        return node

    def term():
        node = factor()
        while peek() in (("sym", "*"), ("sym", "/")):
            node = (take()[1], node, factor())
        return node

    def arguments(name):
        if take_if(("sym", "(")) is None:
            raise ExprError(f"{name} needs brackets: {name}(...).")
        args = [expression()]
        while take_if(("sym", ",")):
            args.append(expression())
        if take_if(("sym", ")")) is None:
            raise ExprError(f"{name}'s bracket is not closed.")
        return args

    def call(name):
        args = arguments(name)
        if name == "IF":
            if len(args) not in (2, 3):
                raise ExprError("IF takes a condition, a value if true and optionally a value if false.")
            return ("if", args[0], args[1], args[2] if len(args) == 3 else None)
        if name == "SWITCH":
            if len(args) < 3:
                raise ExprError("SWITCH takes a value, then pairs of match and result, and optionally a default.")
            subject, rest = args[0], args[1:]
            default = rest.pop() if len(rest) % 2 else None
            return ("switch", subject, list(zip(rest[0::2], rest[1::2])), default)
        if name == "NOT":
            if len(args) != 1:
                raise ExprError("NOT takes one condition.")
            return ("not", args[0])
        if len(args) < 2:
            raise ExprError(f"{name} takes two or more conditions.")
        return (name.lower(), args)

    def factor():
        token = peek()
        if token is None:
            raise ExprError("The expression ends too early.")
        if token == ("sym", "-"):
            take()
            return ("neg", factor())
        if token == ("sym", "("):
            take()
            node = expression()
            if take_if(("sym", ")")) is None:
                raise ExprError("A bracket is not closed.")
            return node
        take()
        if token[0] == "ref":
            return ("ref", token[1])
        if token[0] == "num":
            return ("num", float(token[1]))
        if token[0] == "str":
            return ("str", token[1])
        if token[0] == "name":
            if token[1] not in FUNCTIONS:
                raise ExprError(f"Unknown function '{token[1]}'. Use {', '.join(FUNCTIONS)}.")
            return call(token[1])
        raise ExprError(f"Unexpected '{token[1]}'.")

    tree = expression()
    if pos != len(tokens):
        raise ExprError(f"Unexpected '{tokens[pos][1]}'.")
    check_values(tree)
    return tree


def children(tree) -> list:
    kind = tree[0]
    if kind in ("ref", "num", "str"):
        return []
    if kind in ("neg", "not"):
        return [tree[1]]
    if kind == "cmp":
        return [tree[2], tree[3]]
    if kind == "if":
        return [x for x in tree[1:] if x is not None]
    if kind == "switch":
        return [tree[1]] + [x for pair in tree[2] for x in pair] + ([tree[3]] if tree[3] is not None else [])
    if kind in ("and", "or"):
        return list(tree[1])
    return [tree[1], tree[2]]


def check_values(tree, value=True) -> None:
    """Text may only be compared with; a result must be a number (or a condition)."""
    kind = tree[0]
    if kind == "str" and value:
        raise ExprError(f'Text ("{tree[1]}") can only be compared with, e.g. [Region] = "{tree[1]}".')
    if kind == "cmp":
        check_values(tree[2], False)
        check_values(tree[3], False)
    elif kind == "switch":
        check_values(tree[1], False)
        for match, result in tree[2]:
            check_values(match, False)
            check_values(result)
        if tree[3] is not None:
            check_values(tree[3])
    else:
        for child in children(tree):
            check_values(child)


def refs(tree) -> list[str]:
    if tree[0] == "ref":
        return [tree[1]]
    return [r for child in children(tree) for r in refs(child)]


def compared_refs(tree) -> set[str]:
    """References used only as something to compare (these may be text columns)."""
    out = set()
    if tree[0] == "cmp":
        out |= {side[1] for side in tree[2:] if side[0] == "ref"}
    if tree[0] == "switch" and tree[1][0] == "ref":
        out.add(tree[1][1])
    for child in children(tree):
        out |= compared_refs(child)
    return out


def numeric_refs(tree) -> list[str]:
    """References whose values are calculated with, so must be numbers."""
    if tree[0] == "ref":
        return [tree[1]]
    if tree[0] == "cmp":
        return [r for side in tree[2:] if side[0] != "ref" for r in numeric_refs(side)]
    if tree[0] == "switch":
        out = [] if tree[1][0] == "ref" else numeric_refs(tree[1])
        for match, result in tree[2]:
            out += numeric_refs(result) + ([] if match[0] in ("ref", "str", "num") else numeric_refs(match))
        return out + (numeric_refs(tree[3]) if tree[3] is not None else [])
    return [r for child in children(tree) for r in numeric_refs(child)]


def _sql_string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def to_sql(tree, ref_sql) -> str:
    kind = tree[0]
    if kind == "ref":
        return ref_sql(tree[1])
    if kind == "num":
        return repr(tree[1])
    if kind == "str":
        return _sql_string(tree[1])
    if kind == "neg":
        return f"(-{to_sql(tree[1], ref_sql)})"
    if kind == "cmp":
        return f"({to_sql(tree[2], ref_sql)} {tree[1]} {to_sql(tree[3], ref_sql)})"
    if kind == "if":
        otherwise = to_sql(tree[3], ref_sql) if tree[3] is not None else "NULL"
        return f"(CASE WHEN {to_sql(tree[1], ref_sql)} THEN {to_sql(tree[2], ref_sql)} ELSE {otherwise} END)"
    if kind == "switch":
        whens = " ".join(f"WHEN {to_sql(m, ref_sql)} THEN {to_sql(r, ref_sql)}" for m, r in tree[2])
        otherwise = to_sql(tree[3], ref_sql) if tree[3] is not None else "NULL"
        return f"(CASE {to_sql(tree[1], ref_sql)} {whens} ELSE {otherwise} END)"
    if kind in ("and", "or"):
        return "(" + f" {kind.upper()} ".join(to_sql(x, ref_sql) for x in tree[1]) + ")"
    if kind == "not":
        return f"(NOT {to_sql(tree[1], ref_sql)})"
    left, right = to_sql(tree[1], ref_sql), to_sql(tree[2], ref_sql)
    if kind == "/":
        return f"({left} / NULLIF({right}, 0))"
    return f"({left} {kind} {right})"


def _compare(op: str, left, right):
    if left is None or right is None:
        return None
    return {"=": left == right, "<>": left != right, "<": left < right, ">": left > right,
            "<=": left <= right, ">=": left >= right}[op]


def evaluate(tree, values: dict):
    """A formula over measure values; a blank in a condition counts as false, as in DAX."""
    kind = tree[0]
    if kind == "ref":
        return values.get(tree[1])
    if kind in ("num", "str"):
        return tree[1]
    if kind == "neg":
        value = evaluate(tree[1], values)
        return None if value is None else -value
    if kind == "cmp":
        return _compare(tree[1], evaluate(tree[2], values), evaluate(tree[3], values))
    if kind == "if":
        if evaluate(tree[1], values):
            return evaluate(tree[2], values)
        return evaluate(tree[3], values) if tree[3] is not None else None
    if kind == "switch":
        subject = evaluate(tree[1], values)
        for match, result in tree[2]:
            if subject is not None and subject == evaluate(match, values):
                return evaluate(result, values)
        return evaluate(tree[3], values) if tree[3] is not None else None
    if kind == "and":
        return all(evaluate(x, values) for x in tree[1])
    if kind == "or":
        return any(evaluate(x, values) for x in tree[1])
    if kind == "not":
        value = evaluate(tree[1], values)
        return None if value is None else not value
    left, right = evaluate(tree[1], values), evaluate(tree[2], values)
    if left is None or right is None:
        return None
    if kind == "+":
        return left + right
    if kind == "-":
        return left - right
    if kind == "*":
        return left * right
    return None if right == 0 else left / right
