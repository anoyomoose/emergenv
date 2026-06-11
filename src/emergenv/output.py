"""Internal output routing for emergenv.

These are internal knobs (no CLI flags wire to them yet):

- ``log_mode`` controls where :func:`log` writes: ``LOG_STDOUT`` (default),
  ``LOG_STDERR`` (e.g. to keep stdout clean for piped data) or ``LOG_OFF``
  (quiet). Errors (:func:`emergenv.cli.error`) always go to stderr and are never
  silenced.
- ``age_mode`` controls how the ``age`` subprocess's stderr is handled:
  ``AGE_CAPTURE`` (default - captured and surfaced in our error messages),
  ``AGE_STDERR`` (let age write straight to our stderr) or ``AGE_OFF`` (discard).

Set them by assigning the module attributes, e.g. ``output.log_mode =
output.LOG_OFF``.
"""

from __future__ import annotations

import subprocess
import sys

LOG_STDOUT = "stdout"
LOG_STDERR = "stderr"
LOG_OFF = "off"

AGE_CAPTURE = "capture"
AGE_STDERR = "stderr"
AGE_OFF = "off"

log_mode = LOG_STDOUT
age_mode = AGE_CAPTURE


def log(message: str) -> None:
    """Report progress, per :data:`log_mode` (stdout / stderr / off)."""
    if log_mode == LOG_OFF:
        return
    stream = sys.stderr if log_mode == LOG_STDERR else sys.stdout
    print(message, file=stream)


def age_stderr_arg() -> int | None:
    """The subprocess ``stderr`` argument for the current :data:`age_mode`."""
    if age_mode == AGE_STDERR:
        return None  # inherit our stderr
    if age_mode == AGE_OFF:
        return subprocess.DEVNULL
    return subprocess.PIPE  # AGE_CAPTURE
