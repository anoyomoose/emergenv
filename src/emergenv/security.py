"""Trust pre-flight for the path to every ``authorized_keys`` file.

Before any operation that ENCRYPTS (``encrypt``, ``edit``, ``rekey``) we verify
that the recipient lists cannot have been tampered with by another user.

The threat: if anyone but you or root can write to an ``authorized_keys`` file -
or to any directory on the path to it, which would let them replace the file or
redirect the path by renaming a directory - they can add their own public key as
a recipient. Your next encryption then silently encrypts secrets to them.
Verify-on-write does *not* catch this: it round-trips with *your* key, and an
attacker *adds* a key rather than removing yours, so the round-trip still passes.
A static check of the trust path is the only thing that closes the hole.

This is a guard, not a guarantee: it is inherently TOCTOU (permissions could
change between this check and the actual write). It closes the realistic risk - a
persistently mis-permissioned store - rather than promising atomicity.

The whole check is POSIX-only. It rests on Unix ownership, permission bits and
group semantics, none of which map onto Windows ACLs, and the ``grp``/``pwd``
modules it needs do not exist there. On Windows it is therefore disabled
entirely: :func:`recipient_trust_violations` returns nothing and securing the
store is the user's responsibility (NTFS permissions). See the README's Security
section.
"""

from __future__ import annotations

import os
import stat
import sys

from . import EmergenvError
from .paths import data_dir

# grp/pwd are POSIX-only and absent on Windows; guard the import so the module
# (and therefore the whole CLI) stays importable there. They are only ever used
# from the POSIX branch of recipient_trust_violations.
if sys.platform != "win32":
    import grp
    import pwd


class InsecureStoreError(EmergenvError):
    """An ``authorized_keys`` file, or a directory on the path to it, is writable
    by - or owned by - someone other than you or root, so its recipient list
    could be substituted. Raised before encrypting; the CLI maps it to its own
    exit code so it is never confused with an ordinary failure."""


def _trusted_owner(uid: int) -> bool:
    """True if ``uid`` is root or the current effective user (nobody else)."""
    return uid == 0 or uid == os.geteuid()


def _shared_group_reason(gid: int) -> str | None:
    """Why group ``gid`` makes a group-writable path unsafe, or ``None`` if it
    does not.

    Group-write only grants access to *members* of the group, so a group-writable
    path is harmless when you are its only member (the "user private group" setup
    that umask 002 implies). We verify that: ``None`` means you are provably the
    sole member; a string explains why it is considered unsafe - either another
    member exists, or membership could not be enumerated (in which case we are
    conservative and treat it as unsafe).

    Note ``getpwall`` may not enumerate networked directories (LDAP/SSSD); it can
    therefore miss a remote user whose *primary* group is this one. That gap is
    accepted - private groups in practice use local accounts - and the conservative
    fallback covers an outright enumeration failure, never the reverse.

    On macOS the optimization is skipped entirely: users and groups live in
    Directory Services rather than ``/etc/passwd``/``/etc/group`` (which ``getpwall``
    cannot reliably enumerate), and the default primary group ``staff`` is shared by
    every local user - so there is no "user private group" and group-writable
    genuinely means shared. Group-writable is therefore always treated as unsafe.
    """
    try:
        group = grp.getgrgid(gid)
    except KeyError:
        return f"membership of gid {gid} could not be verified"
    name = group.gr_name
    if sys.platform == "darwin":
        return (
            f"group '{name}' is shared by default on macOS (membership not enumerable)"
        )
    try:
        my_name: str | None = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        my_name = None

    if any(member != my_name for member in group.gr_mem):  # supplementary members
        return f"group '{name}' has other members"
    try:  # users whose *primary* group is this gid are not listed in gr_mem
        people = pwd.getpwall()
    except OSError:
        return f"group '{name}' membership could not be verified"
    if not people:  # an empty passwd means enumeration is not working
        return f"group '{name}' membership could not be verified"
    me = os.geteuid()
    if any(person.pw_gid == gid and person.pw_uid != me for person in people):
        return f"group '{name}' has other members"
    return None


def _write_bit_problem(mode: int, *, sticky_ok: bool, gid: int) -> str | None:
    """The write-permission problem for a path, or ``None`` if its write bits are
    safe. ``sticky_ok`` lets a directory's sticky bit forgive all write bits."""
    if sticky_ok and mode & stat.S_ISVTX:
        return None  # sticky: only owners can rename/delete, so writes are safe
    if mode & 0o002:
        return "world-writable" + ("" if not sticky_ok else " without the sticky bit")
    if mode & 0o020:
        reason = _shared_group_reason(gid)
        if reason is not None:
            return f"group-writable ({reason})"
    return None


def _dir_problems(st: os.stat_result) -> list[str]:
    """Reasons a directory on the trust path is unsafe (empty list = fine).

    World-writable is unsafe unless the sticky bit is set (e.g. ``/tmp`` at
    ``1777``): on a sticky directory only a file's owner (or root) can rename or
    delete it, so nobody else can substitute the ``authorized_keys``.
    Group-writable is unsafe only when the group has a member other than you (see
    :func:`_shared_group_reason`). Must also be owned by you or root.
    """
    problems = []
    write = _write_bit_problem(st.st_mode, sticky_ok=True, gid=st.st_gid)
    if write is not None:
        problems.append(write)
    if not _trusted_owner(st.st_uid):
        problems.append(f"owned by uid {st.st_uid}, not you or root")
    return problems


def _file_problems(st: os.stat_result) -> list[str]:
    """Reasons the ``authorized_keys`` file itself is unsafe (empty list = fine).

    Must not be world-writable, nor group-writable when the group has another
    member, and must be owned by you or root. Read bits are irrelevant - it holds
    public keys - so ``0644`` and ``0600`` are both fine.
    """
    problems = []
    write = _write_bit_problem(st.st_mode, sticky_ok=False, gid=st.st_gid)
    if write is not None:
        problems.append(write)
    if not _trusted_owner(st.st_uid):
        problems.append(f"owned by uid {st.st_uid}, not you or root")
    return problems


def recipient_trust_violations() -> list[str]:
    """Every trust-path problem found, as ``"<path>: <reason>"`` strings.

    Checks *every* ``authorized_keys`` under the data dir: the file itself, plus
    every directory from ``/`` down to the one that holds it. Paths are resolved
    to their canonical location first, so a symlinked component is checked where
    its bytes actually live. Directory checks are de-duplicated across the shared
    ancestry, and every problem is collected (not just the first) so the whole
    store can be fixed in one pass. An empty list means the store is safe; a
    missing data dir yields ``[]`` - the command itself raises "run init".

    On Windows the check is disabled (Unix-only; see the module docstring) and
    this always returns ``[]``.
    """
    if sys.platform == "win32":
        return []
    base = data_dir()
    if not base.is_dir():
        return []

    violations: list[str] = []
    checked_dirs: set[str] = set()
    for ak in sorted(p for p in base.rglob("authorized_keys") if p.is_file()):
        resolved = ak.resolve()
        try:
            file_st = resolved.stat()
        except OSError:
            continue
        for reason in _file_problems(file_st):
            violations.append(f"{resolved}: {reason}")

        for directory in [resolved.parent, *resolved.parent.parents]:
            key = str(directory)
            if key in checked_dirs:
                continue
            checked_dirs.add(key)
            try:
                dir_st = directory.stat()
            except OSError:
                continue
            for reason in _dir_problems(dir_st):
                violations.append(f"{directory}: {reason}")

    return violations


def describe_trust_violations(violations: list[str]) -> str:
    """Render ``violations`` as a human-readable explanation plus fix hint."""
    return (
        "a recipient list could be tampered with by another user. An "
        "authorized_keys file (or a directory on the path to it) is writable by, "
        "or owned by, someone other than you or root, so its public keys could "
        "be swapped and your secrets silently encrypted to an attacker. Fix these "
        "(e.g. 'chmod go-w <path>', or 'chown') and retry. A path flagged because "
        "group membership 'could not be verified' is harmless if you are the only "
        "member of that group - 'chmod g-w' silences it:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def check_recipient_trust() -> None:
    """Raise :class:`InsecureStoreError` if any recipient list could be tampered
    with. Called as a pre-flight before encrypting (``encrypt``/``edit``/
    ``rekey``); ``status`` instead surfaces the same finding as a warning."""
    violations = recipient_trust_violations()
    if violations:
        raise InsecureStoreError(
            "refusing to encrypt: " + describe_trust_violations(violations)
        )
