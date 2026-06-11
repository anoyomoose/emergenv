"""Encryption helpers built on the external ``age`` binary.

Invariants enforced here (and relied upon by the rest of emergenv):

- Every ``age`` invocation lives in this module; no other module shells out.
- Encryption and decryption happen entirely **in memory** (stdin -> stdout).
- Ciphertext is ONLY ever produced by :func:`encrypt_verified`, which decrypts
  its own output in memory and checks it round-trips to the original plaintext
  before returning. Nothing else may write a ``.age`` file.
- Decryption offers ``age`` every identity whose public half is listed in
  ``authorized_keys`` (or the single ``EMERGENV_KEY`` override) and lets ``age``
  pick whichever actually decrypts the file.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from . import EmergenvError, output
from .paths import data_dir, nearest_authorized_keys

MIN_AGE_VERSION = (1, 0, 0)

# Candidate private keys tried for decryption, in order. Only those whose
# ``.pub`` is listed in authorized_keys (and which are readable) are offered.
IDENTITY_CANDIDATES = (
    "/etc/ssh/ssh_host_ed25519_key",
    "/etc/ssh/ssh_host_rsa_key",
    "~/.ssh/id_ed25519",
    "~/.ssh/id_rsa",
)

# Only these SSH algorithms are supported as recipients/identities.
SUPPORTED_KEY_TYPES = ("ssh-ed25519", "ssh-rsa")

KEY_OVERRIDE_ENV = "EMERGENV_KEY"


def _parse_version(output: str) -> tuple[int, int, int] | None:
    """Extract a ``(major, minor, patch)`` tuple from ``age --version`` output.

    ``age`` prints something like ``v1.1.1``. Returns ``None`` if no
    semantic version can be found (e.g. an ``(unknown)`` dev build).
    """
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", output)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def age_check() -> bool:
    """Return True if the ``age`` binary is callable and at least 1.0.0."""
    if shutil.which("age") is None:
        return False
    try:
        result = subprocess.run(
            ["age", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    version = _parse_version(result.stdout + result.stderr)
    return version is not None and version >= MIN_AGE_VERSION


def _key_material(line: str) -> str | None:
    """Return the ``"<type> <base64>"`` core of a public key line, or None.

    Comments, options and trailing whitespace are ignored so that the same key
    matches regardless of how it is annotated.
    """
    parts = line.split()
    for i, token in enumerate(parts):
        if token in SUPPORTED_KEY_TYPES and i + 1 < len(parts):
            return f"{token} {parts[i + 1]}"
    return None


def _authorized_materials() -> set[str]:
    """Key materials listed in *any* authorized_keys file under the data dir.

    Decryption only needs to know which of the candidate private keys are worth
    offering to ``age`` (which then enforces the actual per-file recipients), so
    the union across every ``authorized_keys`` is the right set.
    """
    files = [p for p in data_dir().rglob("authorized_keys") if p.is_file()]
    if not files:
        raise EmergenvError(f"no authorized_keys found under {data_dir()}")
    materials = set()
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            material = _key_material(line)
            if material is not None:
                materials.add(material)
    return materials


def select_identities() -> list[Path]:
    """Return the private keys to offer ``age`` for decryption.

    ``EMERGENV_KEY``, if set, overrides everything with a single identity.
    Otherwise every readable candidate whose ``.pub`` is listed in
    authorized_keys is returned, and ``age`` decides which one decrypts.
    """
    override = os.environ.get(KEY_OVERRIDE_ENV)
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise EmergenvError(f"{KEY_OVERRIDE_ENV} is not a file: {path}")
        return [path]

    authorized = _authorized_materials()
    identities = []
    for candidate in IDENTITY_CANDIDATES:
        private = Path(candidate).expanduser()
        public = private.with_name(private.name + ".pub")
        if not private.is_file() or not public.is_file():
            continue
        if not os.access(private, os.R_OK):
            continue
        material = _key_material(public.read_text(encoding="utf-8"))
        if material is not None and material in authorized:
            identities.append(private)

    if not identities:
        raise EmergenvError(
            "no usable decryption key found - none of the candidate SSH keys "
            f"are listed in any authorized_keys under {data_dir()} "
            f"(or set {KEY_OVERRIDE_ENV} to a private key)"
        )
    return identities


def _run_age(args: list[str], data: bytes) -> bytes:
    """Run ``age`` with ``data`` on stdin and return stdout, raising on failure."""
    try:
        result = subprocess.run(
            ["age", *args],
            input=data,
            stdout=subprocess.PIPE,  # age's stdout is the data; always captured
            stderr=output.age_stderr_arg(),
        )
    except OSError as exc:
        raise EmergenvError(f"failed to run age: {exc}") from exc
    if result.returncode != 0:
        detail = ""
        if result.stderr:
            detail = ": " + result.stderr.decode("utf-8", "replace").strip()
        raise EmergenvError(f"age failed{detail}")
    return result.stdout


def encrypt_bytes(plaintext: bytes, recipients: Path) -> bytes:
    """Encrypt ``plaintext`` in memory to the given recipients file."""
    if not recipients.is_file():
        raise EmergenvError(f"missing recipients file: {recipients}")
    return _run_age(["-R", str(recipients), "-o", "-"], plaintext)


def decrypt_bytes(ciphertext: bytes) -> bytes:
    """Decrypt ``ciphertext`` in memory using the selected identities."""
    args = ["-d"]
    for identity in select_identities():
        args += ["-i", str(identity)]
    args += ["-o", "-"]
    return _run_age(args, ciphertext)


def inaccessible_authorized_keys() -> list[Path]:
    """Return every ``authorized_keys`` set under the data dir you cannot use.

    For each ``authorized_keys`` file a throwaway probe is encrypted to that set
    and decryption is attempted with your identities (the same selection real
    decryption uses, honouring ``EMERGENV_KEY``). Any set whose probe does not
    round-trip is one you are not a recipient of - so you could not encrypt, edit
    or rekey files governed by it. An empty/recipient-less set fails to encrypt
    and is reported too. Everything is in memory; nothing is written.
    """
    probe = b"emergenv access check"
    base = data_dir()
    gaps: list[Path] = []
    for path in sorted(base.rglob("authorized_keys")):
        if not path.is_file():
            continue
        try:
            if decrypt_bytes(encrypt_bytes(probe, path)) != probe:
                gaps.append(path)
        except EmergenvError:
            gaps.append(path)
    return gaps


def encrypt_verified(plaintext: bytes, dest: Path) -> bytes:
    """Encrypt ``plaintext`` for ``dest`` and confirm it round-trips.

    This is the ONLY function that should produce ciphertext destined for a
    ``.age`` file. Recipients are the ``authorized_keys`` nearest ``dest``
    (walking up to the data dir). It encrypts, then decrypts its own output, and
    aborts unless the result is byte-identical to the input.
    """
    recipients = nearest_authorized_keys(dest)
    ciphertext = encrypt_bytes(plaintext, recipients)
    try:
        roundtrip = decrypt_bytes(ciphertext)
    except EmergenvError as exc:
        raise EmergenvError(
            f"encryption verification failed: the result could not be decrypted "
            f"with any available key - are you a recipient in {recipients}? ({exc})"
        ) from exc
    if roundtrip != plaintext:
        raise EmergenvError(
            "encryption round-trip verification failed - refusing to write a "
            "file that does not decrypt back to its plaintext"
        )
    return ciphertext
