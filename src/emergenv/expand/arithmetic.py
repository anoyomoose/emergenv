from __future__ import annotations

"""Integer arithmetic for $(( expr )) blocks.

A small tokeniser + precedence-climbing parser. Side-effect-free operators only.
Bare names resolve from the namespace as integers. '$' is illegal anywhere.
'/' and '%' truncate toward zero (C/bash semantics), not Python floor.
"""

import re
from typing import Callable, Mapping

from .errors import ExpansionError

# Maximum number of bits allowed in an intermediate or final result.
_MAX_BITS = 4096

# Multi-char operators must be tried before their single-char prefixes.
_TOKEN = re.compile(
    r"""\s*(?:
        (?P<num>0[xXoObB][0-9a-fA-F]+|\d+) |
        (?P<name>[A-Za-z_][A-Za-z0-9_]*) |
        (?P<op>\*\*|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^~!()<>?:])
    )""",
    re.VERBOSE,
)

# Pattern for valid variable values: optional sign, then decimal / 0x / 0o / 0b.
# Underscores and bare leading-zero forms (010, 08) are rejected.
_VAR_VALUE = re.compile(r"^[+-]?(?:0[xXoObB][0-9a-fA-F]+|0|[1-9][0-9]*)$")


def _tokenise(expr: str) -> list[tuple[str, str]]:
    if "$" in expr:
        raise ExpansionError("'$' is not allowed inside $(( ))")
    if not expr.strip():
        raise ExpansionError("empty arithmetic expression")
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        m = _TOKEN.match(expr, pos)
        if m is None or m.end() == pos:
            raise ExpansionError(f"invalid token in arithmetic at {expr[pos:]!r}")
        kind = m.lastgroup
        assert kind is not None
        tokens.append((kind, m.group(kind)))
        pos = m.end()
    tokens.append(("end", ""))
    return tokens


def _parse_int(text: str) -> int:
    # Reject ambiguous leading zero (like bash 010)
    if (
        len(text) > 1
        and text[0] == "0"
        and text[1:2]
        not in (
            "x",
            "X",
            "o",
            "O",
            "b",
            "B",
        )
    ):
        raise ExpansionError(f"ambiguous integer literal (use 0o for octal): {text!r}")
    try:
        return int(text, 0)
    except ValueError:
        raise ExpansionError(
            f"ambiguous or invalid integer literal: {text!r}"
        ) from None


def _parse_var_int(name: str, raw_value: str) -> int:
    """Parse an integer from a variable value, with strict rules.

    Accepts decimal / 0x / 0o / 0b with an optional leading +/-.
    Rejects underscores, ambiguous leading-zero forms, and non-integers.
    """
    stripped = raw_value.strip()
    if not _VAR_VALUE.match(stripped):
        # Distinguish ambiguous-leading-zero from truly non-integer.
        # A string that looks like it starts with 0[digit] after optional sign
        # is the ambiguous-octal case; everything else is a non-integer.
        core = stripped.lstrip("+-")
        if len(core) > 1 and core[0] == "0" and core[1:2].isdigit():
            raise ExpansionError(
                f"ambiguous integer literal (use 0o for octal): {stripped!r}"
            )
        raise ExpansionError(f"variable {name!r} is not an integer: {raw_value!r}")
    # The regex guarantees the string is structurally valid; int() must succeed.
    return int(stripped, 0)


def _trunc_div(a: int, b: int) -> int:
    if b == 0:
        raise ExpansionError("division by zero")
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _trunc_mod(a: int, b: int) -> int:
    if b == 0:
        raise ExpansionError("modulo by zero")
    return a - _trunc_div(a, b) * b


def _pow(a: int, b: int) -> int:
    if b < 0:
        raise ExpansionError("negative exponent in arithmetic")
    # Guard against computing enormous results.
    if b * max(1, a.bit_length()) > _MAX_BITS:
        raise ExpansionError("arithmetic result too large")
    result: int = pow(a, b)
    return result


def _checked_shift_left(a: int, b: int) -> int:
    if b < 0:
        raise ExpansionError("shift count out of range: negative shift")
    if b > _MAX_BITS:
        raise ExpansionError("shift count out of range")
    result = a << b
    if result.bit_length() > _MAX_BITS:
        raise ExpansionError("arithmetic result too large")
    return result


def _checked_shift_right(a: int, b: int) -> int:
    if b < 0:
        raise ExpansionError("shift count out of range: negative shift")
    return a >> b


# Left-binding power per binary operator (higher binds tighter). Ternary and
# unary are handled specially.
_BinaryFn = Callable[[int, int], int]

_BINARY: dict[str, tuple[int, _BinaryFn]] = {
    "||": (1, lambda a, b: int(bool(a) or bool(b))),
    "&&": (2, lambda a, b: int(bool(a) and bool(b))),
    "|": (3, lambda a, b: a | b),
    "^": (4, lambda a, b: a ^ b),
    "&": (5, lambda a, b: a & b),
    "==": (6, lambda a, b: int(a == b)),
    "!=": (6, lambda a, b: int(a != b)),
    "<": (7, lambda a, b: int(a < b)),
    "<=": (7, lambda a, b: int(a <= b)),
    ">": (7, lambda a, b: int(a > b)),
    ">=": (7, lambda a, b: int(a >= b)),
    "<<": (8, _checked_shift_left),
    ">>": (8, _checked_shift_right),
    "+": (9, lambda a, b: a + b),
    "-": (9, lambda a, b: a - b),
    "*": (10, lambda a, b: a * b),
    "/": (10, _trunc_div),
    "%": (10, _trunc_mod),
}


class _Parser:
    def __init__(
        self, tokens: list[tuple[str, str]], variables: Mapping[str, str]
    ) -> None:
        self.tokens = tokens
        self.i = 0
        self.variables = variables

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.i]

    def advance(self) -> tuple[str, str]:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def expect(self, value: str) -> None:
        _, text = self.advance()
        if text != value:
            raise ExpansionError(
                f"expected {value!r} in arithmetic, got {text or 'end'!r}"
            )

    def parse(self) -> int:
        result = self.expr(0)
        if self.peek()[0] != "end":
            raise ExpansionError(f"unexpected token {self.peek()[1]!r} in arithmetic")
        return result

    def expr(self, min_bp: int) -> int:
        left = self.unary()
        while True:
            kind, text = self.peek()
            if text == "?" and min_bp <= 0:
                self.advance()
                then = self.expr(0)
                self.expect(":")
                otherwise = self.expr(0)
                left = then if left else otherwise
                continue
            if kind != "op" or text not in _BINARY:
                break
            bp, fn = _BINARY[text]
            if bp < min_bp:
                break
            self.advance()
            right = self.expr(bp + 1)
            left = fn(left, right)
        return left

    def unary(self) -> int:
        _, text = self.peek()
        if text in ("-", "+", "~", "!"):
            self.advance()
            operand = self.unary()
            if text == "-":
                return -operand
            if text == "+":
                return operand
            if text == "~":
                return ~operand
            return int(not operand)
        return self.power()

    def power(self) -> int:
        base = self.primary()
        if self.peek()[1] == "**":
            self.advance()
            exponent = self.unary()  # right-assoc, allows -exp via unary
            return _pow(base, exponent)
        return base

    def primary(self) -> int:
        kind, text = self.advance()
        if kind == "num":
            return _parse_int(text)
        if kind == "name":
            value = self.variables.get(text)
            if value is None:
                raise ExpansionError(f"undefined variable in arithmetic: {text!r}")
            return _parse_var_int(text, value)
        if text == "(":
            inner = self.expr(0)
            self.expect(")")
            return inner
        raise ExpansionError(f"unexpected token {text or 'end'!r} in arithmetic")


def evaluate(expr: str, variables: Mapping[str, str]) -> int:
    """Evaluate an integer arithmetic expression against ``variables``."""
    return _Parser(_tokenise(expr), variables).parse()
