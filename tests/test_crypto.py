"""Tests for the age-backed crypto helpers."""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import requires_age

from emergenv import EmergenvError, crypto

# --- version parsing --------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("v1.1.1", (1, 1, 1)),
        ("1.0.0", (1, 0, 0)),
        ("age version v1.2.3\n", (1, 2, 3)),
        ("(unknown)", None),
        ("", None),
        ("no digits here", None),
    ],
)
def test_parse_version(text: str, expected: tuple[int, int, int] | None) -> None:
    assert crypto._parse_version(text) == expected


# --- public-key material extraction ----------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("ssh-ed25519 AAAAC3Nz comment", "ssh-ed25519 AAAAC3Nz"),
        ("ssh-rsa AAAAB3Nz user@host", "ssh-rsa AAAAB3Nz"),
        ('command="x",no-pty ssh-ed25519 AAAAKEY note', "ssh-ed25519 AAAAKEY"),
        ("ssh-ed25519 AAAAONLY", "ssh-ed25519 AAAAONLY"),
        ("# a comment", None),
        ("", None),
        ("ecdsa-sha2-nistp256 AAAA", None),  # unsupported type ignored
        ("ssh-ed25519", None),  # type but no key body
    ],
)
def test_key_material(line: str, expected: str | None) -> None:
    assert crypto._key_material(line) == expected


def test_key_material_ignores_comment_differences() -> None:
    a = crypto._key_material("ssh-ed25519 AAAAKEY alice@laptop")
    b = crypto._key_material("ssh-ed25519 AAAAKEY bob@server")
    assert a == b


# --- age_check (monkeypatched) ---------------------------------------------


def test_age_check_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert crypto.age_check() is False


def test_age_check_old_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/age")

    def fake_run(*_a: Any, **_k: Any) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess([], 0, stdout="v0.9.0", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert crypto.age_check() is False


def test_age_check_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/age")

    def fake_run(*_a: Any, **_k: Any) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess([], 0, stdout="v1.0.0", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert crypto.age_check() is True


# --- select_identities ------------------------------------------------------


def test_select_identities_override(workdir: SimpleNamespace) -> None:
    # EMERGENV_KEY is set by the fixture to the throwaway key.
    assert crypto.select_identities() == [workdir.keypair.private]


def test_select_identities_override_missing_file(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EMERGENV_KEY", str(workdir.root / "nope"))
    with pytest.raises(EmergenvError, match="EMERGENV_KEY"):
        crypto.select_identities()


def test_select_identities_from_authorized_keys(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EMERGENV_KEY", raising=False)
    monkeypatch.setattr(crypto, "IDENTITY_CANDIDATES", (str(workdir.keypair.private),))
    assert crypto.select_identities() == [workdir.keypair.private]


def test_select_identities_none_match(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EMERGENV_KEY", raising=False)
    # authorized_keys lists our key, but the candidate is a different key.
    other = workdir.root / "other"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(other), "-N", "", "-q"], check=True
    )
    monkeypatch.setattr(crypto, "IDENTITY_CANDIDATES", (str(other),))
    with pytest.raises(EmergenvError, match="no usable decryption key"):
        crypto.select_identities()


def test_select_identities_skips_keys_without_pub(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EMERGENV_KEY", raising=False)
    lonely = workdir.root / "lonely"
    lonely.write_text("PRIVATE")  # no .pub sibling
    monkeypatch.setattr(crypto, "IDENTITY_CANDIDATES", (str(lonely),))
    with pytest.raises(EmergenvError, match="no usable decryption key"):
        crypto.select_identities()


# --- encrypt / decrypt / verify (real age) ---------------------------------


@requires_age
def test_roundtrip(workdir: SimpleNamespace) -> None:
    plaintext = b"FOO=bar\nBAZ=qux\n"
    ciphertext = crypto.encrypt_verified(plaintext, workdir.data / "x.age")
    assert ciphertext.startswith(b"age-encryption.org/v1")
    assert crypto.decrypt_bytes(ciphertext) == plaintext


@requires_age
def test_roundtrip_empty(workdir: SimpleNamespace) -> None:
    assert (
        crypto.decrypt_bytes(crypto.encrypt_verified(b"", workdir.data / "x.age"))
        == b""
    )


@requires_age
def test_ciphertext_is_nondeterministic(workdir: SimpleNamespace) -> None:
    recipients = workdir.data / "authorized_keys"
    a = crypto.encrypt_bytes(b"same\n", recipients)
    b = crypto.encrypt_bytes(b"same\n", recipients)
    assert a != b  # justifies the no-rewrite optimisation
    assert crypto.decrypt_bytes(a) == crypto.decrypt_bytes(b) == b"same\n"


@requires_age
def test_encrypt_verified_refuses_on_bad_roundtrip(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crypto, "decrypt_bytes", lambda _: b"corrupted")
    with pytest.raises(EmergenvError, match="round-trip"):
        crypto.encrypt_verified(b"important\n", workdir.data / "x.age")


@requires_age
def test_encrypt_requires_authorized_keys(workdir: SimpleNamespace) -> None:
    (workdir.data / "authorized_keys").unlink()
    with pytest.raises(EmergenvError, match="authorized_keys"):
        crypto.encrypt_verified(b"data\n", workdir.data / "x.age")


@requires_age
def test_decrypt_with_wrong_key_fails(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    ciphertext = crypto.encrypt_verified(b"secret\n", workdir.data / "x.age")
    other = workdir.root / "wrong"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(other), "-N", "", "-q"], check=True
    )
    monkeypatch.setenv("EMERGENV_KEY", str(other))
    with pytest.raises(EmergenvError, match="age failed"):
        crypto.decrypt_bytes(ciphertext)
