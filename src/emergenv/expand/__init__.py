"""A safe, stdlib-only subset of shell-style variable/arithmetic expansion.

Public API:

    expand(template, variables) -> str
    ExpansionError

See EXPANSION.md for the exact supported syntax and semantics. The engine never
executes commands and reads no environment of its own: callers pass a flat
``name -> value`` mapping, where an absent key means "unset".
"""

from __future__ import annotations

from .engine import expand
from .errors import ExpansionError

__all__ = ["expand", "ExpansionError"]
