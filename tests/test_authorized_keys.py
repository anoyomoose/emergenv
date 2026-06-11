"""Tests for per-directory authorized_keys: nearest-wins recipients, whole-file
overrides that narrow access deeper in the tree, the union used for decryption,
and the EMERGENV_KEY override."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import requires_age

from emergenv import EmergenvError, crypto

pytestmark = requires_age


def _keygen(path: Path) -> Path:
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(path), "-N", "", "-q"], check=True
    )
    return path


def test_subdir_keys_narrow_decryption(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subdir authorized_keys is a whole-file override: an outer/base key that
    isn't in the inner set cannot decrypt files written there."""
    key_b = _keygen(workdir.root / "keyB")
    prod = workdir.data / "prod"
    prod.mkdir()
    # prod narrows the recipient set to key B ONLY (the base key A is excluded)
    (prod / "authorized_keys").write_text((workdir.root / "keyB.pub").read_text())

    age = prod / "db.age"
    monkeypatch.setenv("EMERGENV_KEY", str(key_b))  # encrypt as a recipient
    ciphertext = crypto.encrypt_verified(b"X=1\n", age)

    # the inner key decrypts it
    assert crypto.decrypt_bytes(ciphertext) == b"X=1\n"

    # the outer/base key A, absent from prod/authorized_keys, cannot
    monkeypatch.setenv("EMERGENV_KEY", str(workdir.keypair.private))
    with pytest.raises(EmergenvError, match="age failed"):
        crypto.decrypt_bytes(ciphertext)


def test_encrypt_verify_fails_when_not_a_recipient(workdir: SimpleNamespace) -> None:
    """Encrypting to a set you aren't in fails verification with a clear error."""
    _keygen(workdir.root / "keyB")
    prod = workdir.data / "prod"
    prod.mkdir()
    (prod / "authorized_keys").write_text((workdir.root / "keyB.pub").read_text())
    # EMERGENV_KEY is key A (fixture) - not a recipient of prod's key-B-only set
    with pytest.raises(EmergenvError, match="recipient"):
        crypto.encrypt_verified(b"X=1\n", prod / "db.age")


def test_select_identities_unions_all_authorized_keys(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate key listed only in a *subdir* authorized_keys is still offered."""
    monkeypatch.delenv("EMERGENV_KEY", raising=False)
    (workdir.data / "authorized_keys").write_text("")  # base lists nothing
    sub = workdir.data / "prod"
    sub.mkdir()
    (sub / "authorized_keys").write_text(workdir.keypair.public)  # A only in subdir
    monkeypatch.setattr(crypto, "IDENTITY_CANDIDATES", (str(workdir.keypair.private),))
    assert crypto.select_identities() == [workdir.keypair.private]


def test_emergenv_key_overrides_authorized_keys(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EMERGENV_KEY decrypts regardless of authorized_keys - even with none at all."""
    key_b = _keygen(workdir.root / "keyB")
    recipients = workdir.root / "recip"
    recipients.write_text((workdir.root / "keyB.pub").read_text())
    ciphertext = crypto.encrypt_bytes(b"X=1\n", recipients)

    (workdir.data / "authorized_keys").unlink()  # no authorized_keys anywhere
    monkeypatch.setenv("EMERGENV_KEY", str(key_b))
    assert crypto.decrypt_bytes(ciphertext) == b"X=1\n"
