from __future__ import annotations

"""Apply a ${VAR<op>...} parameter-expansion form to the namespace.

`expand` (injected by the engine) expands nested ${...} in sub-parts. In word
and replacement positions the nested result is literal text; in PATTERN
positions it is glob-escaped so a substituted '*' matches literally.
"""

import re
from typing import Callable, Mapping

from . import glob
from .errors import ExpansionError
from .scanner import LITERAL, VAR, scan

Expand = Callable[[str, Mapping[str, str]], str]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _lookup(name: str, variables: Mapping[str, str]) -> str | None:
    return variables.get(name)


def _require(name: str, variables: Mapping[str, str]) -> str:
    value = _lookup(name, variables)
    if value is None:
        raise ExpansionError(f"undefined variable: {name!r}")
    return value


def _expand_pattern(template: str, variables: Mapping[str, str], expand: Expand) -> str:
    """Expand a glob pattern: literal segments keep glob syntax; substituted
    segments are glob-escaped so their metacharacters match literally."""
    out: list[str] = []
    for seg in scan(template):
        if seg.kind == LITERAL:
            out.append(seg.text)
        elif seg.kind == VAR:
            out.append(glob.escape(expand("${" + seg.text + "}", variables)))
        else:  # ARITH
            out.append(glob.escape(expand("$((" + seg.text + "))", variables)))
    return "".join(out)


def _parse_int_arg(token: str, variables: Mapping[str, str], expand: Expand) -> int:
    raw = token.strip()
    # If the token contains a ${...} reference, expand it first
    if "${" in raw:
        raw = expand(raw, variables).strip()
    try:
        return int(raw, 10)
    except ValueError:
        raise ExpansionError(f"expected an integer index, got {raw!r}") from None


def _find_top_level_colon(args: str) -> int:
    """Return index of first ':' at brace-depth 0, or -1 if none."""
    depth = 0
    for i, ch in enumerate(args):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == ":" and depth == 0:
            return i
    return -1


def _substring(
    name: str, args: str, variables: Mapping[str, str], expand: Expand
) -> str:
    value = _require(name, variables)
    cut = _find_top_level_colon(args)
    if cut == -1:
        offset = _parse_int_arg(args, variables, expand)
        length = None
    else:
        offset = _parse_int_arg(args[:cut], variables, expand)
        length = _parse_int_arg(args[cut + 1 :], variables, expand)
    n = len(value)
    start = offset if offset >= 0 else max(0, n + offset)
    start = min(start, n)
    if length is None:
        end = n
    elif length >= 0:
        end = min(start + length, n)
    else:
        end = max(start, n + length)
    return value[start:end]


def _find_top_level_slash(spec: str) -> int:
    """Return index of first '/' at brace-depth 0 that is not backslash-escaped, or -1."""
    depth = 0
    i = 0
    while i < len(spec):
        ch = spec[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "/" and depth == 0:
            return i
        i += 1
    return -1


def expand_var(body: str, variables: Mapping[str, str], expand: Expand) -> str:
    """Expand the body of a ${...} segment against the namespace."""
    # -----------------------------------------------------------------------
    # ${#VAR} — length
    # -----------------------------------------------------------------------
    if body.startswith("#"):
        name = body[1:]
        if not _NAME.match(name):
            raise ExpansionError(f"invalid variable name: {name!r}")
        return str(len(_require(name, variables)))

    # Parse the variable name at the start of the body
    m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", body)
    if m is None:
        raise ExpansionError(f"invalid variable name in {body!r}")
    name = m.group(0)
    rest = body[m.end() :]

    # -----------------------------------------------------------------------
    # Plain reference: ${VAR}
    # -----------------------------------------------------------------------
    if rest == "":
        return _require(name, variables)

    # -----------------------------------------------------------------------
    # Defaults / required / alternate
    # IMPORTANT: check these BEFORE the substring path so that ${VAR:-d} is a
    # default and ${VAR: -2} (colon-space) reaches the substring path.
    # -----------------------------------------------------------------------
    colon = False
    sign = ""
    word = ""

    op2 = rest[:2]
    if op2 in (":-", ":?", ":+"):
        word, colon, sign = rest[2:], True, op2[1]
    elif rest[0] in "-?+":
        word, colon, sign = rest[1:], False, rest[0]

    if sign:
        value = _lookup(name, variables)
        unset = value is None
        empty = value == ""
        trigger = (unset or empty) if colon else unset
        if sign == "-":
            return (
                expand(word, variables)
                if trigger
                else (value if value is not None else "")
            )
        if sign == "+":
            return expand(word, variables) if not trigger else ""
        # sign == "?"
        if trigger:
            msg = expand(word, variables) if word else f"{name}: required"
            raise ExpansionError(msg)
        return value if value is not None else ""

    # -----------------------------------------------------------------------
    # Substring: ${VAR:offset} or ${VAR:offset:length}
    # (reached when rest[0] == ':' and the op is NOT ':-', ':?', or ':+')
    # -----------------------------------------------------------------------
    if rest[0] == ":":
        return _substring(name, rest[1:], variables, expand)

    # -----------------------------------------------------------------------
    # Prefix / suffix removal
    # -----------------------------------------------------------------------
    if rest[0] == "#":
        longest = rest[1:2] == "#"
        pat_src = rest[2:] if longest else rest[1:]
        pat = _expand_pattern(pat_src, variables, expand)
        return glob.remove_prefix(_require(name, variables), pat, longest=longest)

    if rest[0] == "%":
        longest = rest[1:2] == "%"
        pat_src = rest[2:] if longest else rest[1:]
        pat = _expand_pattern(pat_src, variables, expand)
        return glob.remove_suffix(_require(name, variables), pat, longest=longest)

    # -----------------------------------------------------------------------
    # Search / replace
    # -----------------------------------------------------------------------
    if rest[0] == "/":
        all_ = rest[1:2] == "/"
        spec = rest[2:] if all_ else rest[1:]
        anchor: str | None = None
        if spec[:1] == "#":
            anchor, spec = "start", spec[1:]
        elif spec[:1] == "%":
            anchor, spec = "end", spec[1:]
        cut = _find_top_level_slash(spec)
        pat_src = spec if cut == -1 else spec[:cut]
        repl_src = "" if cut == -1 else spec[cut + 1 :]
        pat = _expand_pattern(pat_src, variables, expand)
        repl = expand(repl_src, variables)
        return glob.replace(
            _require(name, variables), pat, repl, all_=all_, anchor=anchor
        )

    # -----------------------------------------------------------------------
    # Case modification
    # Only the exact forms ^, ^^, ,, ,, are supported; anything else
    # (pattern forms like ${V^x} or ${V^^^}) falls through to the error.
    # -----------------------------------------------------------------------
    if rest[0] == "^":
        if rest == "^":
            value = _require(name, variables)
            return value[:1].upper() + value[1:] if value else ""
        if rest == "^^":
            return _require(name, variables).upper()

    if rest[0] == ",":
        if rest == ",":
            value = _require(name, variables)
            return value[:1].lower() + value[1:] if value else ""
        if rest == ",,":
            return _require(name, variables).lower()

    raise ExpansionError(f"unsupported parameter expansion: ${{{body}}}")
