"""Tests for the ``encrypt`` command."""

from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

from emergenv import crypto

pytestmark = requires_age


def test_encrypt_single(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("database.env", "FOO=bar\n")
    result = run("encrypt", "database")
    assert result.code == 0
    age = workdir.data / "database.age"
    assert age.is_file()
    assert not (workdir.data / "database.env").exists()  # deleted by default
    assert crypto.decrypt_bytes(age.read_bytes()) == b"FOO=bar\n"


def test_encrypt_target_base_in_store(workdir: SimpleNamespace, run: Run) -> None:
    # A target base living in emergenv/ is just a fragment named "<target>.emerg"
    # — encrypt/decrypt stay inside the data dir, no special-casing needed.
    workdir.write_env("prod.emerg.env", "FOO=bar\n")
    result = run("encrypt", "prod.emerg")
    assert result.code == 0
    age = workdir.data / "prod.emerg.age"
    assert age.is_file()
    assert crypto.decrypt_bytes(age.read_bytes()) == b"FOO=bar\n"


def test_encrypt_keep(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("database.env", "FOO=bar\n")
    run("encrypt", "--keep", "database")
    assert (workdir.data / "database.env").exists()


def test_encrypt_missing_env(workdir: SimpleNamespace, run: Run) -> None:
    result = run("encrypt", "database")
    assert result.code == 1
    assert "does not exist" in result.err


def test_encrypt_accepts_prefixed_and_suffixed_name(
    workdir: SimpleNamespace, run: Run
) -> None:
    workdir.write_env("database.env", "FOO=bar\n")
    assert run("encrypt", "emergenv/database.env").code == 0
    assert (workdir.data / "database.age").is_file()


def test_encrypt_all_recurses(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("a.env", "A=1\n")
    workdir.write_env("prod/b.env", "B=2\n")
    result = run("encrypt", "--all")
    assert result.code == 0
    assert (workdir.data / "a.age").is_file()
    assert (workdir.data / "prod" / "b.age").is_file()
    assert not (workdir.data / "a.env").exists()
    assert not (workdir.data / "prod" / "b.env").exists()


def test_encrypt_all_keep(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("a.env", "A=1\n")
    run("encrypt", "--all", "--keep")
    assert (workdir.data / "a.env").exists()


def test_encrypt_no_rewrite_when_unchanged(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("database.env", "FOO=bar\n")
    run("encrypt", "--keep", "database")
    before = (workdir.data / "database.age").read_bytes()

    result = run("encrypt", "--keep", "database")
    after = (workdir.data / "database.age").read_bytes()
    assert after == before  # not rewritten (age is non-deterministic)
    assert "unchanged" in result.out


def test_encrypt_rewrites_when_changed(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("database.env", "FOO=bar\n")
    run("encrypt", "--keep", "database")
    before = (workdir.data / "database.age").read_bytes()

    workdir.write_env("database.env", "FOO=changed\n")
    run("encrypt", "--keep", "database")
    after = (workdir.data / "database.age").read_bytes()
    assert after != before
    assert crypto.decrypt_bytes(after) == b"FOO=changed\n"


def test_encrypt_requires_name_or_all(workdir: SimpleNamespace, run: Run) -> None:
    assert run("encrypt").code == 1


def test_encrypt_rejects_name_and_all(workdir: SimpleNamespace, run: Run) -> None:
    assert run("encrypt", "database", "--all").code == 1


def test_encrypt_rejects_escaping_name(workdir: SimpleNamespace, run: Run) -> None:
    result = run("encrypt", "../evil")
    assert result.code == 1
    assert "evil" in result.err


def test_encrypt_missing_authorized_keys(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("database.env", "FOO=bar\n")
    (workdir.data / "authorized_keys").unlink()
    result = run("encrypt", "database")
    assert result.code == 1
    assert "authorized_keys" in result.err


def test_encrypt_refuses_unverifiable_and_keeps_plaintext(
    workdir: SimpleNamespace, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the round-trip check fails, no .age is written and the .env survives."""
    workdir.write_env("database.env", "FOO=bar\n")
    monkeypatch.setattr(crypto, "decrypt_bytes", lambda _: b"corrupted")
    result = run("encrypt", "database")
    assert result.code == 1
    assert "round-trip" in result.err
    assert not (workdir.data / "database.age").exists()  # nothing written
    assert (workdir.data / "database.env").exists()  # plaintext preserved
