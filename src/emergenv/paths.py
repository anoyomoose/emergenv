"""Filesystem locations and fragment resolution for emergenv.

All commands operate strictly within the ``emergenv/`` data directory under the
working directory. To make that guarantee enforceable in one place, every path
a command touches MUST be derived from the helpers here:

- :func:`working_dir` / :func:`data_dir` are the only sources of truth for
  *where* emergenv operates.
- :func:`resolve_name` turns a user-supplied ``<fragment>`` into an absolute base
  path and refuses anything that would escape :func:`data_dir` (``..``,
  absolute paths, symlinked-out targets).

A ``<fragment>`` is a path *relative to the data directory, without an extension*.
It may contain ``/`` to reach into profile/target subdirectories. Users may, for
shell-completion convenience, also write a leading ``emergenv/`` prefix and/or a
trailing ``.age``/``.env`` extension; both are stripped.
"""

from __future__ import annotations

from pathlib import Path

from . import EmergenvError

DATA_DIR_NAME = "emergenv"
ENV_SUFFIX = ".env"
AGE_SUFFIX = ".age"


def working_dir() -> Path:
    """The current working directory at invocation.

    Targets (``<target>.emerg.*``) and build output stay anchored here; only the
    ``emergenv/`` data directory is discovered (see :func:`data_dir`).
    """
    return Path.cwd()


def _discover_root() -> Path:
    """Walk up from the cwd to the directory containing ``emergenv/``.

    Returns the first directory (starting at the cwd) that contains an
    ``emergenv/`` directory, without crossing a ``.git`` boundary (the repo
    root) - so a sibling project's or your home directory's store is never
    picked up by accident. Falls back to the cwd when none is found.
    """
    start = Path.cwd()
    current = start
    while True:
        if (current / DATA_DIR_NAME).is_dir():
            return current
        if (current / ".git").exists():  # .git dir or worktree/submodule file
            return start
        parent = current.parent
        if parent == current:  # filesystem root
            return start
        current = parent


def data_dir() -> Path:
    """The ``emergenv/`` data directory, discovered by walking up from the cwd.

    Found in the cwd or the nearest ancestor containing ``emergenv/``, not
    crossing a ``.git`` boundary. Falls back to ``<cwd>/emergenv`` when none is
    found, so :func:`require_data_dir` reports the usual "run init" error.
    """
    return _discover_root() / DATA_DIR_NAME


def require_data_dir() -> Path:
    """Return :func:`data_dir`, or raise if it does not exist."""
    base = data_dir()
    if not base.is_dir():
        raise EmergenvError(
            f"no '{DATA_DIR_NAME}/' directory here - run 'emergenv init' first"
        )
    return base


def nearest_authorized_keys(path: Path) -> Path:
    """The closest ``authorized_keys`` from ``path``'s directory up to data_dir.

    Per-directory ``authorized_keys`` files are whole-file overrides of the base
    ``emergenv/authorized_keys`` — they do not extend each other — so the one
    nearest a file wins. Raises if none exists from the file's directory up to
    (and including) :func:`data_dir`.
    """
    root = data_dir().resolve()
    directory = path.resolve().parent
    if directory != root and root not in directory.parents:
        raise EmergenvError(f"path escapes the '{DATA_DIR_NAME}/' directory: {path}")
    current = directory
    while True:
        candidate = current / "authorized_keys"
        if candidate.is_file():
            return candidate
        if current == root:
            raise EmergenvError(
                f"no authorized_keys found from {directory} up to {root}"
            )
        current = current.parent


def age_file(base: Path) -> Path:
    """The ``.age`` file for an extension-less base path."""
    return base.with_name(base.name + AGE_SUFFIX)


def env_file(base: Path) -> Path:
    """The ``.env`` file for an extension-less base path."""
    return base.with_name(base.name + ENV_SUFFIX)


def validate_component(cleaned: str, *, kind: str = "fragment") -> None:
    """Enforce the rules shared by ``<fragment>`` and ``<target>``.

    ``cleaned`` must already have any caller-specific prefix/suffix stripped.
    Raises :class:`EmergenvError` when it is empty, ``.``, absolute, contains
    ``..``, or has ``emergenv`` as its first path component (which would be
    ambiguous with the stripped ``emergenv/`` prefix). Centralised so the
    reserved names cannot drift between :func:`resolve_name` and target
    resolution; ``kind`` (``"fragment"`` or ``"target"``) tailors the messages.
    """
    if not cleaned:
        raise EmergenvError(f"a <{kind}> is required")
    if cleaned == ".":
        raise EmergenvError(
            f"'.' is not a valid {kind}; use 'dot' to refer to the .env target"
        )
    if cleaned.startswith("/"):
        raise EmergenvError(f"a {kind} must not start with '/': {cleaned!r}")
    if ".." in Path(cleaned).parts:
        raise EmergenvError(f"a {kind} must not contain '..': {cleaned!r}")
    if Path(cleaned).parts[0] == DATA_DIR_NAME:
        raise EmergenvError(f"'{DATA_DIR_NAME}' is a reserved {kind}: {cleaned!r}")


def resolve_name(name: str) -> Path:
    """Resolve a user-supplied ``<fragment>`` to an absolute, extension-less base path.

    Strips an optional ``emergenv/`` prefix and a trailing ``.age``/``.env``
    extension, applies :func:`validate_component`, then verifies the result
    stays inside :func:`data_dir`. The names ``.`` (use ``dot`` for the ``.env``
    target) and ``emergenv`` are reserved.
    """
    cleaned = name.strip()
    prefix = DATA_DIR_NAME + "/"
    if cleaned.startswith(prefix):
        cleaned = cleaned[len(prefix) :]
    for suffix in (AGE_SUFFIX, ENV_SUFFIX):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break

    validate_component(cleaned, kind="fragment")

    base = data_dir()
    resolved = (base / cleaned).resolve()
    root = base.resolve()
    if resolved == root:
        raise EmergenvError(
            f"invalid fragment (resolves to the '{DATA_DIR_NAME}/' directory itself): {name!r}"
        )
    if root not in resolved.parents:
        raise EmergenvError(
            f"fragment escapes the '{DATA_DIR_NAME}/' directory: {name!r}"
        )
    return resolved


def iter_pairs() -> list[dict]:
    """List every ``<fragment>`` in the data directory and its available formats.

    Walks :func:`data_dir` recursively, grouping ``.age``/``.env`` files by their
    extension-less base. Returns records sorted by name, each a dict with keys
    ``name`` (str, relative POSIX path), ``age`` (Path | None) and ``env``
    (Path | None).
    """
    base = data_dir()
    pairs: dict[str, dict] = {}
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix == AGE_SUFFIX:
            kind = "age"
        elif path.suffix == ENV_SUFFIX:
            kind = "env"
        else:
            continue
        stem = path.with_name(path.name[: -len(path.suffix)])
        name = stem.relative_to(base).as_posix()
        record = pairs.setdefault(name, {"name": name, "age": None, "env": None})
        record[kind] = path
    return [pairs[name] for name in sorted(pairs)]
