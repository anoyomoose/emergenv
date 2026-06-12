"""Recipient-trust pre-flight: the path to every authorized_keys must be safe.

The unit tests drive ``recipient_trust_violations`` directly with a hand-built
data dir (no ``age`` needed); foreign ownership is simulated by monkeypatching
``os.geteuid`` so user-owned paths look like someone else's. The CLI tests check
that encrypt-family commands abort with exit 253 and that ``status`` warns but
keeps reporting.
"""

from __future__ import annotations

import grp
import os
import pwd
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

from emergenv import cli, security
from emergenv.security import InsecureStoreError, recipient_trust_violations


def _store(tmp_path: Path) -> Path:
    """A minimal data dir with a base authorized_keys; returns the data dir."""
    data = tmp_path / "emergenv"
    data.mkdir()
    (data / "authorized_keys").write_text("ssh-ed25519 AAAAEXAMPLE base\n")
    return data


class _FakeGroup:
    def __init__(self, name: str, members: list[str]) -> None:
        self.gr_name = name
        self.gr_mem = members


class _FakePasswd:
    def __init__(self, name: str, uid: int, gid: int) -> None:
        self.pw_name = name
        self.pw_uid = uid
        self.pw_gid = gid


# --- unit: the trust walk ---------------------------------------------------


def test_clean_store_has_no_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _store(tmp_path)
    assert recipient_trust_violations() == []


def test_missing_data_dir_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no emergenv/ at all
    assert recipient_trust_violations() == []


def test_world_writable_dir_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    data.chmod(0o777)  # world-writable, no sticky
    violations = recipient_trust_violations()
    assert any("world-writable" in v and str(data) in v for v in violations)


def test_sticky_world_writable_dir_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    data.chmod(0o1777)  # world-writable + sticky, like /tmp
    assert recipient_trust_violations() == []


def test_group_writable_dir_with_other_member_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    data.chmod(0o775)  # group-writable, no sticky
    monkeypatch.setattr(
        security, "_shared_group_reason", lambda gid: "group 'devs' has other members"
    )
    violations = recipient_trust_violations()
    assert any("group-writable" in v and str(data) in v for v in violations)


def test_group_writable_dir_when_alone_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole point of (B): if you are the only member of the group, a
    # group-writable directory is harmless and must not be flagged.
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    data.chmod(0o775)
    monkeypatch.setattr(security, "_shared_group_reason", lambda gid: None)
    assert recipient_trust_violations() == []


def test_group_writable_dir_unverifiable_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Conservative fallback: if membership cannot be verified, flag it anyway.
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    data.chmod(0o775)
    monkeypatch.setattr(
        security,
        "_shared_group_reason",
        lambda gid: "group 'x' membership could not be verified",
    )
    assert any("could not be verified" in v for v in recipient_trust_violations())


def test_group_writable_with_sticky_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sticky protects against rename/delete by non-owners, so a sticky directory
    # is safe even when group- (or world-) writable - just like /tmp at 1777.
    # Sticky short-circuits before any membership lookup.
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    data.chmod(0o1775)
    assert recipient_trust_violations() == []


# --- unit: the lonely-group determination -----------------------------------


def test_shared_group_reason_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(grp, "getgrgid", lambda gid: _FakeGroup("jorrit", []))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: _FakePasswd("jorrit", 1000, 1000))
    monkeypatch.setattr(
        pwd,
        "getpwall",
        lambda: [_FakePasswd("root", 0, 0), _FakePasswd("jorrit", 1000, 1000)],
    )
    assert security._shared_group_reason(1000) is None


def test_shared_group_reason_supplementary_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(grp, "getgrgid", lambda gid: _FakeGroup("devs", ["alice"]))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: _FakePasswd("jorrit", 1000, 1000))
    assert "other members" in (security._shared_group_reason(2000) or "")


def test_shared_group_reason_primary_member_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(grp, "getgrgid", lambda gid: _FakeGroup("shared", []))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: _FakePasswd("jorrit", 1000, 1000))
    monkeypatch.setattr(
        pwd,
        "getpwall",
        lambda: [_FakePasswd("jorrit", 1000, 5000), _FakePasswd("bob", 1001, 5000)],
    )
    assert "other members" in (security._shared_group_reason(5000) or "")


def test_shared_group_reason_macos_always_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On macOS the lonely-group optimization is skipped: group-writable is always
    # treated as shared, regardless of what enumeration would (unreliably) say.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(grp, "getgrgid", lambda gid: _FakeGroup("staff", []))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: _FakePasswd("jorrit", 501, 20))
    monkeypatch.setattr(pwd, "getpwall", lambda: [_FakePasswd("jorrit", 501, 20)])
    reason = security._shared_group_reason(20)
    assert reason is not None and "macOS" in reason


def test_shared_group_reason_unknown_group(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(gid: int) -> object:
        raise KeyError(gid)

    monkeypatch.setattr(grp, "getgrgid", boom)
    assert "could not be verified" in (security._shared_group_reason(424242) or "")


def test_shared_group_reason_enumeration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(grp, "getgrgid", lambda gid: _FakeGroup("shared", []))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: _FakePasswd("jorrit", 1000, 1000))

    def boom() -> object:
        raise OSError("nss unavailable")

    monkeypatch.setattr(pwd, "getpwall", boom)
    assert "could not be verified" in (security._shared_group_reason(5000) or "")


def test_writable_authorized_keys_file_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    (data / "authorized_keys").chmod(0o666)  # world-writable file
    violations = recipient_trust_violations()
    assert any("authorized_keys" in v and "writable" in v for v in violations)


def test_group_writable_file_with_other_member_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    (data / "authorized_keys").chmod(0o664)  # group-writable file
    monkeypatch.setattr(
        security, "_shared_group_reason", lambda gid: "group 'devs' has other members"
    )
    violations = recipient_trust_violations()
    assert any("authorized_keys" in v and "group-writable" in v for v in violations)


def test_readable_authorized_keys_file_is_fine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Read bits don't matter - it's public keys; 0644 and 0600 are both fine.
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    (data / "authorized_keys").chmod(0o644)
    assert recipient_trust_violations() == []
    (data / "authorized_keys").chmod(0o600)
    assert recipient_trust_violations() == []


def test_foreign_owner_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _store(tmp_path)
    # Pretend we are some other non-root user: every user-owned path looks foreign.
    monkeypatch.setattr(os, "geteuid", lambda: 999999)
    assert any("not you or root" in v for v in recipient_trust_violations())


def test_every_authorized_keys_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    sub = data / "production"
    sub.mkdir()
    nested = sub / "authorized_keys"
    nested.write_text("ssh-ed25519 AAAAEXAMPLE prod\n")
    nested.chmod(0o666)  # only the nested set is insecure
    violations = recipient_trust_violations()
    assert any("production/authorized_keys" in v for v in violations)


# --- CLI integration --------------------------------------------------------


@requires_age
def test_encrypt_aborts_on_insecure_store(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("database.env", "SECRET=1\n")
    workdir.data.chmod(0o777)  # world-writable data dir
    try:
        r = run("encrypt", "database")
    finally:
        workdir.data.chmod(0o755)
    assert r.code == 253
    assert "refusing to encrypt" in r.err
    # nothing was encrypted; the plaintext is untouched
    assert (workdir.data / "database.env").read_text() == "SECRET=1\n"


@requires_age
def test_rekey_aborts_on_insecure_store(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "SECRET=1\n")
    workdir.data.chmod(0o777)
    try:
        r = run("rekey")
    finally:
        workdir.data.chmod(0o755)
    assert r.code == 253


@requires_age
def test_decrypt_is_not_gated(workdir: SimpleNamespace, run: Run) -> None:
    # decrypt adds no recipients; an insecure store must not block it.
    workdir.write_age("database.age", "SECRET=1\n")
    workdir.data.chmod(0o777)
    try:
        r = run("decrypt", "database")
    finally:
        workdir.data.chmod(0o755)
    assert r.code == 0


@requires_age
def test_status_warns_and_exits_253(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "SECRET=1\n")
    workdir.data.chmod(0o777)
    try:
        r = run("status")
    finally:
        workdir.data.chmod(0o755)
    assert r.code == 253  # security finding dominates the graded status code
    assert "warning: insecure store" in r.err
    assert "database" in r.out  # but it still produced its listing


@requires_age
def test_status_clean_store_unaffected(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "SECRET=1\n")
    r = run("status")
    assert r.code == 0
    assert "insecure store" not in r.err


def test_check_raises_insecurestoreerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    data.chmod(0o777)
    with pytest.raises(InsecureStoreError, match="refusing to encrypt"):
        security.check_recipient_trust()


# --- Windows: the whole check is disabled -----------------------------------


def test_windows_disables_trust_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _store(tmp_path)
    data.chmod(0o777)  # would be flagged on POSIX
    monkeypatch.setattr(sys, "platform", "win32")
    assert recipient_trust_violations() == []
    security.check_recipient_trust()  # must not raise


def test_write_private_degrades_without_fchmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # os.fchmod is absent on Windows; _write_private must still write the file.
    monkeypatch.delattr(os, "fchmod", raising=False)
    target = tmp_path / "out.env"
    cli._write_private(target, b"SECRET=1\n")
    assert target.read_bytes() == b"SECRET=1\n"
