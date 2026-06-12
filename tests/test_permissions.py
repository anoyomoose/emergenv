"""The uncommitted plaintext ``.env`` files emergenv writes must be owner-only
(0o600): decrypted fragments and built outputs hold real secrets, and a write
must never leave one group- or world-readable, even when overwriting a file that
already was.

Committed artefacts (``.age``, ``authorized_keys``, ``.gitignore``) and the data
directory are intentionally *not* restricted - git resets their modes on checkout
regardless - so they are not asserted here.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

pytestmark = requires_age


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_decrypt_creates_private_env(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "SECRET=1\n")
    run("decrypt", "database")
    assert _mode(workdir.data / "database.env") == 0o600


def test_decrypt_all_creates_private_env(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("a.age", "A=1\n")
    workdir.write_age("b.age", "B=2\n")
    run("decrypt", "--all")
    assert _mode(workdir.data / "a.env") == 0o600
    assert _mode(workdir.data / "b.env") == 0o600


def test_decrypt_all_tightens_preexisting_loose_env(
    workdir: SimpleNamespace, run: Run
) -> None:
    workdir.write_age("database.age", "SECRET=1\n")
    stale = workdir.data / "database.env"
    stale.write_text("OLD=0\n")
    os.chmod(stale, 0o644)
    run("decrypt", "--all")  # overwrites the loose .env with fresh plaintext
    assert _mode(stale) == 0o600
    assert stale.read_text() == "SECRET=1\n"


def test_edit_env_is_private_during_wait(
    workdir: SimpleNamespace, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir.write_age("database.age", "SECRET=1\n")
    seen: list[int] = []

    def fake_input(prompt: str = "") -> str:
        seen.append(_mode(workdir.data / "database.env"))
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    run("edit", "--wait", "database")
    assert seen == [0o600]  # the decrypted file was private while exposed on disk


def test_build_output_is_private(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("SECRET=1\n")
    run("build", "dot")
    assert _mode(workdir.root / ".env") == 0o600
