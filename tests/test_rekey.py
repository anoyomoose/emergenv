"""Tests for the ``rekey`` command: re-encrypt every fragment to its current
recipients, in two in-memory passes (verify-all, then write-all)."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

from emergenv import EmergenvError, crypto

pytestmark = requires_age


def _keygen(path: Path) -> Path:
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(path), "-N", "", "-q"], check=True
    )
    return path


def test_rekey_always_rewrites_age(workdir: SimpleNamespace, run: Run) -> None:
    age = workdir.write_age("db.age", "X=1\n")
    before = age.read_bytes()

    result = run("rekey")
    assert result.code == 0
    assert "rekeyed" in result.out

    after = age.read_bytes()
    assert after != before  # always rewritten, even though plaintext is unchanged
    assert crypto.decrypt_bytes(after) == b"X=1\n"


def test_rekey_applies_new_recipient(
    workdir: SimpleNamespace, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: after adding a key to authorized_keys, rekey lets that key
    decrypt every fragment."""
    age = workdir.write_age("db.age", "X=1\n")  # encrypted to key A (the fixture key)
    key_b = _keygen(workdir.root / "keyB")

    # Before rekey, key B is not a recipient and cannot decrypt.
    monkeypatch.setenv("EMERGENV_KEY", str(key_b))
    with pytest.raises(EmergenvError):
        crypto.decrypt_bytes(age.read_bytes())

    # Add key B to the recipient set and rekey (decrypting as A, the fixture key).
    monkeypatch.setenv("EMERGENV_KEY", str(workdir.keypair.private))
    ak = workdir.data / "authorized_keys"
    ak.write_text(ak.read_text() + (workdir.root / "keyB.pub").read_text())
    assert run("rekey").code == 0

    # Now key B decrypts it.
    monkeypatch.setenv("EMERGENV_KEY", str(key_b))
    assert crypto.decrypt_bytes(age.read_bytes()) == b"X=1\n"


def test_rekey_keeps_matching_env_untouched(workdir: SimpleNamespace, run: Run) -> None:
    age = workdir.write_age("db.age", "D=1\n")
    env = workdir.write_env("db.env", "D=1\n")  # matches the .age plaintext
    before = age.read_bytes()

    assert run("rekey").code == 0

    assert env.exists() and env.read_text() == "D=1\n"  # left in place, untouched
    assert age.read_bytes() != before  # .age still rewritten
    assert crypto.decrypt_bytes(age.read_bytes()) == b"D=1\n"


def test_rekey_adopts_env_only_fragment(workdir: SimpleNamespace, run: Run) -> None:
    env = workdir.write_env("solo.env", "S=1\n")  # no .age beside it

    assert run("rekey").code == 0

    age = workdir.data / "solo.age"
    assert age.is_file()  # .age created
    assert env.exists()  # .env not removed (run `clean` for that)
    assert crypto.decrypt_bytes(age.read_bytes()) == b"S=1\n"


def test_rekey_aborts_on_mismatch_writing_nothing(
    workdir: SimpleNamespace, run: Run
) -> None:
    clean = workdir.write_age("a.age", "X=1\n")  # .age only, fine
    workdir.write_age("b.age", "Y=1\n")
    workdir.write_env("b.env", "Y=changed\n")  # b.env differs from b.age -> mismatch
    before = clean.read_bytes()

    result = run("rekey")
    assert result.code == 1
    assert "b" in result.err
    assert "aborted" in result.err
    assert clean.read_bytes() == before  # pass 1 aborted: nothing was written


def test_rekey_mismatch_lists_all_offenders(workdir: SimpleNamespace, run: Run) -> None:
    for name in ("bbb", "ccc"):
        workdir.write_age(name + ".age", "V=1\n")
        workdir.write_env(name + ".env", "V=2\n")

    result = run("rekey")
    assert result.code == 1
    assert "bbb" in result.err and "ccc" in result.err


def test_rekey_aborts_on_undecryptable_writing_nothing(
    workdir: SimpleNamespace, run: Run
) -> None:
    clean = workdir.write_age("good.age", "X=1\n")
    (workdir.data / "bad.age").write_bytes(b"this is not valid age ciphertext\n")
    before = clean.read_bytes()

    result = run("rekey")
    assert result.code == 1
    assert clean.read_bytes() == before  # aborted in pass 1, nothing written


def test_rekey_nothing_to_do(workdir: SimpleNamespace, run: Run) -> None:
    result = run("rekey")
    assert result.code == 0
    assert "nothing to rekey" in result.out
