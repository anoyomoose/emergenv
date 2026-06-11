"""EMERGENV - Encrypted, Merged Environment."""

__version__ = "1.0.0"


class EmergenvError(Exception):
    """A user-facing error.

    Raised by command and helper code when an operation cannot proceed. The CLI
    entry point catches it, prints the message to stderr, and exits non-zero.
    """
