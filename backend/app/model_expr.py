"""A tiny arithmetic language for measures: [Name] references, numbers, + - * / and brackets.

  [Qty] * [Unit Price]          a row expression over one table's columns
  [Revenue] / [Orders]          a formula over other measures

It is parsed into a tree and compiled to SQL (or evaluated in Python) by this module,
so the AI never writes SQL. Division by zero gives a blank, as in DAX's DIVIDE.
"""

import re

TOKEN = re.compile(r"\s*(?:(\[[^\]]+\])|(\d+(?:\.\d+)?)|(.))")


class ExprError(ValueError):
    pass


def tokenize(text: str) -> list[tuple[str, str]]:
    if not isinstance(text, str) or not text.strip():
        raise ExprError("The expression is empty.")
    tokens = []
    position = 0
    text = text.strip()
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match or match.end() == position:
            break
        ref, number, symbol = match.groups()
        if ref:
            tokens.append(("ref", ref[1:-1].strip()))
        elif number:
            tokens.append(("num", number))
        elif symbol and symbol in "+-*/()":
            tokens.append(("sym", symbol))
        elif symbol and not symbol.isspace():
            raise ExprError(f"Unexpected '{symbol}'. Use [Names], numbers, + - * / and brackets.")
        position = match.end()
    return tokens


def parse(text: str):
    """Parse into nested tuples: ("ref", name) | ("num", value) | ("neg", x) | (op, left, right)."""
    tokens = tokenize(text)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def expression():
        node = term()
        while peek() in (("sym", "+"), ("sym", "-")):
            node = (take()[1], node, term())
        return node

    def term():
        node = factor()
        while peek() in (("sym", "*"), ("sym", "/")):
            node = (take()[1], node, factor())
        return node

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
        raise ExprError(f"Unexpected '{token[1]}'.")

    def take_if(token):
        return take() if peek() == token else None

    tree = expression()
    if pos != len(tokens):
        raise ExprError(f"Unexpected '{tokens[pos][1]}'.")
    return tree


def refs(tree) -> list[str]:
    if tree[0] == "ref":
        return [tree[1]]
    if tree[0] == "num":
        return []
    if tree[0] == "neg":
        return refs(tree[1])
    return refs(tree[1]) + refs(tree[2])


def to_sql(tree, ref_sql) -> str:
    kind = tree[0]
    if kind == "ref":
        return ref_sql(tree[1])
    if kind == "num":
        return repr(tree[1])
    if kind == "neg":
        return f"(-{to_sql(tree[1], ref_sql)})"
    left, right = to_sql(tree[1], ref_sql), to_sql(tree[2], ref_sql)
    if kind == "/":
        return f"({left} / NULLIF({right}, 0))"
    return f"({left} {kind} {right})"


def evaluate(tree, values: dict):
    kind = tree[0]
    if kind == "ref":
        return values.get(tree[1])
    if kind == "num":
        return tree[1]
    if kind == "neg":
        value = evaluate(tree[1], values)
        return None if value is None else -value
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
