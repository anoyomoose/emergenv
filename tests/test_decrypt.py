"""Tests for the ``decrypt`` command."""

import subprocess
from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

pytestmark = requires_age


def test_decrypt_single(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    result = run("decrypt", "database")
    assert result.code == 0
    assert (workdir.data / "database.env").read_text() == "FOO=bar\n"


def test_decrypt_missing_age(workdir: SimpleNamespace, run: Run) -> None:
    result = run("decrypt", "database")
    assert result.code == 1
    assert "does not exist" in result.err


def test_decrypt_refuses_to_overwrite_env(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    workdir.write_env("database.env", "FOO=local\n")
    result = run("decrypt", "database")
    assert result.code == 1
    assert "already exists" in result.err
    assert (workdir.data / "database.env").read_text() == "FOO=local\n"  # untouched


def test_decrypt_accepts_prefixed_and_suffixed_name(
    workdir: SimpleNamespace, run: Run
) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    assert run("decrypt", "emergenv/database.age").code == 0
    assert (workdir.data / "database.env").is_file()


def test_decrypt_all_recurses_and_overwrites(
    workdir: SimpleNamespace, run: Run
) -> None:
    workdir.write_age("a.age", "A=1\n")
    workdir.write_age("prod/b.age", "B=2\n")
    workdir.write_env("a.env", "A=stale\n")  # pre-existing, should be overwritten
    result = run("decrypt", "--all")
    assert result.code == 0
    assert (workdir.data / "a.env").read_text() == "A=1\n"
    assert (workdir.data / "prod" / "b.env").read_text() == "B=2\n"


def test_decrypt_requires_name_or_all(workdir: SimpleNamespace, run: Run) -> None:
    assert run("decrypt").code == 1


def test_decrypt_rejects_name_and_all(workdir: SimpleNamespace, run: Run) -> None:
    assert run("decrypt", "database", "--all").code == 1


def test_decrypt_with_wrong_key(
    workdir: SimpleNamespace, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    wrong = workdir.root / "wrong"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(wrong), "-N", "", "-q"], check=True
    )
    monkeypatch.setenv("EMERGENV_KEY", str(wrong))
    result = run("decrypt", "database")
    assert result.code == 1
    assert "age failed" in result.err
