"""The single exception type for the expansion engine."""

from __future__ import annotations


class ExpansionError(Exception):
    """Raised for any failure while expanding a template: an undefined variable,
    malformed syntax, a bad arithmetic expression, etc.

    The message is human-readable and context-free (no file/line); the host
    application is expected to add source context when it catches this.
    """
