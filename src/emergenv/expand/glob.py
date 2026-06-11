"""Glob matching for the ${VAR#pat}/${VAR%pat}/${VAR/pat/str} parameter forms.

Supported metacharacters (per EXPANSION.md): * ? [set] [a-z] [!set], plus
backslash to escape any of them. Patterns translate to anchored regexes.
Prefix/suffix removal scans cut points so "shortest vs longest" is exact;
replace uses a greedy regex.
"""

from __future__ import annotations

import re

from .errors import ExpansionError

_META = "*?[\\"


def escape(text: str) -> str:
    """Backslash-escape every glob metacharacter, so ``text`` matches literally."""
    return "".join("\\" + c if c in _META else c for c in text)


def _translate(pattern: str) -> str:
    """Translate a glob pattern into a (non-anchored) regex string."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\":
            if i + 1 >= n:
                raise ExpansionError("dangling backslash in glob pattern")
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if c == "*":
            out.append(".*")
        elif c == "?":
            out.append(".")
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":  # a ] as the first member is literal
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                raise ExpansionError("unterminated '[' in glob pattern")
            body = pattern[i + 1 : j]
            if body[:1] in ("!", "^"):
                body = "^" + body[1:]
            out.append("[" + body + "]")
            i = j + 1
            continue
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def remove_prefix(value: str, pattern: str, *, longest: bool) -> str:
    rx = re.compile(_translate(pattern), re.DOTALL)
    ends = range(len(value), -1, -1) if longest else range(len(value) + 1)
    for end in ends:
        if rx.fullmatch(value[:end]) is not None:
            return value[end:]
    return value


def remove_suffix(value: str, pattern: str, *, longest: bool) -> str:
    rx = re.compile(_translate(pattern), re.DOTALL)
    starts = range(len(value) + 1) if longest else range(len(value), -1, -1)
    for start in starts:
        if rx.fullmatch(value[start:]) is not None:
            return value[:start]
    return value


def replace(
    value: str, pattern: str, repl: str, *, all_: bool, anchor: str | None
) -> str:
    body = _translate(pattern)
    if anchor == "start":
        rx = re.compile("^(?:" + body + ")", re.DOTALL)
    elif anchor == "end":
        rx = re.compile("(?:" + body + ")$", re.DOTALL)
    else:
        rx = re.compile("(?:" + body + ")", re.DOTALL)
    out: list[str] = []
    pos = 0
    last_end = -1
    for m in rx.finditer(value):
        start, end = m.span()
        if (
            start == end and start == last_end
        ):  # zero-len match right after a consumed match
            continue
        out.append(value[pos:start])
        out.append(repl)
        pos = end
        last_end = end
        if not all_:
            break
    out.append(value[pos:])
    return "".join(out)
