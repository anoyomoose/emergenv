"""The public entry point: expand one template against a flat namespace."""

from __future__ import annotations

from typing import Mapping

from . import arithmetic, parameter
from .scanner import ARITH, LITERAL, scan


def expand(template: str, variables: Mapping[str, str]) -> str:
    """Expand ``template`` against ``variables`` (a flat ``name -> value`` map).

    An absent name is "unset"; a present name mapping to ``""`` is empty. Raises
    :class:`~emergenv.expand.errors.ExpansionError` on any failure. Substituted
    values are inserted literally and never re-scanned (single pass).
    """
    parts: list[str] = []
    for seg in scan(template):
        if seg.kind == LITERAL:
            parts.append(seg.text)
        elif seg.kind == ARITH:
            parts.append(str(arithmetic.evaluate(seg.text, variables)))
        else:  # VAR
            parts.append(parameter.expand_var(seg.text, variables, expand))
    return "".join(parts)
