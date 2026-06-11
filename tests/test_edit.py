"""Tests for the ``edit`` command.

The editor and the ENTER prompt are simulated: ``--wait`` paths monkeypatch
``builtins.input``; the editor path uses a tiny real script so ``subprocess``
is exercised for real.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

from emergenv import EmergenvError, cli, crypto

pytestmark = requires_age


def test_edit_wait_reencrypts_edits(
    workdir: SimpleNamespace, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    age = workdir.write_age("database.age", "OLD=1\n")
    env = workdir.data / "database.env"

    def fake_input(prompt: str = "") -> str:
        env.write_text("NEW=2\n")  # the "user" edits while we wait
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    result = run("edit", "--wait", "database")
    assert result.code == 0
    assert not env.exists()  # removed after re-encryption
    assert crypto.decrypt_bytes(age.read_bytes()) == b"NEW=2\n"


def test_edit_with_editor(
    workdir: SimpleNamespace,
    run: Run,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    age = workdir.write_age("database.age", "OLD=1\n")
    editor = tmp_path / "fake_editor.sh"
    editor.write_text('#!/bin/sh\nprintf "EDITED=yes\\n" > "$1"\n')
    editor.chmod(0o755)
    monkeypatch.setattr(cli, "_resolve_editor", lambda: [str(editor)])

    result = run("edit", "database")
    assert result.code == 0
    assert not (workdir.data / "database.env").exists()
    assert crypto.decrypt_bytes(age.read_bytes()) == b"EDITED=yes\n"


def test_edit_falls_back_to_enter_without_editor(
    workdir: SimpleNamespace, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir.write_age("database.age", "OLD=1\n")
    monkeypatch.setattr(cli, "_resolve_editor", lambda: None)
    called: list[bool] = []

    def fake_input(prompt: str = "") -> str:
        called.append(True)
        return ""

    monkeypatch.setattr("builtins.input", fake_input)

    result = run("edit", "database")
    assert result.code == 0
    assert called  # the ENTER prompt was used


def test_edit_missing_age(workdir: SimpleNamespace, run: Run) -> None:
    result = run("edit", "database")
    assert result.code == 1
    assert "does not exist" in result.err


def test_edit_refuses_when_env_exists(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "OLD=1\n")
    workdir.write_env("database.env", "LOCAL=1\n")
    result = run("edit", "database")
    assert result.code == 1
    assert "already exists" in result.err


def test_edit_no_rewrite_when_unchanged(
    workdir: SimpleNamespace, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    age = workdir.write_age("database.age", "FOO=bar\n")
    before = age.read_bytes()
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # no edits made

    result = run("edit", "--wait", "database")
    assert result.code == 0
    assert age.read_bytes() == before  # not rewritten
    assert "unchanged" in result.out
    assert not (workdir.data / "database.env").exists()


def test_edit_preserves_plaintext_on_encrypt_failure(
    workdir: SimpleNamespace, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If re-encryption fails, the edited .env must survive (no lost work)."""
    age = workdir.write_age("database.age", "OLD=1\n")
    before = age.read_bytes()
    env = workdir.data / "database.env"

    def fake_input(prompt: str = "") -> str:
        env.write_text("NEW=2\n")
        return ""

    monkeypatch.setattr("builtins.input", fake_input)

    def boom(*_args: object) -> None:
        raise EmergenvError("simulated encryption failure")

    monkeypatch.setattr(cli, "encrypt_verified", boom)

    result = run("edit", "--wait", "database")
    assert result.code == 1
    assert env.read_text() == "NEW=2\n"  # edits preserved
    assert age.read_bytes() == before  # original ciphertext intact
