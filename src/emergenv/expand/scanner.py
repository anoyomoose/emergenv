from __future__ import annotations

"""Split an expansion template into literal / ${...} / $((...)) segments.

Only three sequences are special: ``${`` (reference), ``$((`` (arithmetic), and
``$$`` (a literal ``$``). Any other ``$`` is literal. ${...} bodies keep nested
braces intact for the engine to recurse into.
"""

from dataclasses import dataclass

from .errors import ExpansionError

LITERAL = "literal"
VAR = "var"
ARITH = "arith"


@dataclass(frozen=True)
class Segment:
    kind: str
    text: str


def _match_braces(template: str, start: int) -> int:
    """Return the index of the ``}`` that closes the ``{`` at ``start``."""
    depth = 0
    i = start
    n = len(template)
    while i < n:
        c = template[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ExpansionError("unterminated '${'")


def _match_arith(template: str, start: int) -> int:
    """Return the index *just past* the ``))`` that closes the ``((`` at ``start``.

    ``start`` points at the first ``(`` of ``((``.  The function counts every
    ``(`` / ``)`` so that inner grouping parens (e.g. ``((1+2)*3)``) do not
    prematurely close the outer ``$((...))``; the closing ``))`` is found when
    the running depth returns to zero.
    """
    depth = 0
    i = start
    n = len(template)
    while i < n:
        if template[i] == "(":
            depth += 1
        elif template[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ExpansionError("unterminated '$(('")


def scan(template: str) -> list[Segment]:
    """Scan *template* and return a flat list of :class:`Segment` objects."""
    segments: list[Segment] = []
    literal: list[str] = []
    i, n = 0, len(template)

    def flush() -> None:
        if literal:
            segments.append(Segment(LITERAL, "".join(literal)))
            literal.clear()

    while i < n:
        c = template[i]
        if c != "$":
            literal.append(c)
            i += 1
            continue

        nxt = template[i + 1] if i + 1 < n else ""

        if nxt == "$":
            # $$ -> literal $
            literal.append("$")
            i += 2

        elif nxt == "{":
            # ${...} — brace-matched, nested braces preserved
            close = _match_braces(template, i + 1)
            flush()
            segments.append(Segment(VAR, template[i + 2 : close]))
            i = close + 1

        elif template[i + 1 : i + 3] == "((":
            # $((...)) — paren-matched; _match_arith starts at the first (
            # template[i+1] is the first (, template[i+2] is the second (
            end = _match_arith(template, i + 1)
            flush()
            # body sits between the opening (( and the closing ))
            # opening (( is at [i+1..i+2], so body starts at i+3
            # closing )) is at [end-2..end-1], so body ends at end-2
            segments.append(Segment(ARITH, template[i + 3 : end - 2]))
            i = end

        else:
            # Bare $, not followed by { or ((  -> literal
            literal.append("$")
            i += 1

    flush()
    return segments
