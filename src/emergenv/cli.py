"""Command-line interface for emergenv.

Wires up the ``emergenv`` console command. Cross-cutting rules every command
follows:

- All file access is confined to the ``emergenv/`` data directory, via the
  helpers in :mod:`emergenv.paths` (never hand-build a path).
- Progress is logged to stdout (:func:`log`); failures raise
  :class:`EmergenvError`, which :func:`main` prints to stderr and turns into a
  non-zero exit.
- All ``age`` work goes through :mod:`emergenv.crypto`; ciphertext is only ever
  written via :func:`~emergenv.crypto.encrypt_verified`.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from . import EmergenvError, __version__, output
from .crypto import (
    age_check,
    decrypt_bytes,
    encrypt_verified,
    inaccessible_authorized_keys,
)
from .merge import _Builder, build_target, build_with_trace
from .output import colourize, log
from .paths import (
    AGE_SUFFIX,
    DATA_DIR_NAME,
    ENV_SUFFIX,
    age_file,
    data_dir,
    env_file,
    iter_pairs,
    require_data_dir,
    resolve_name,
    validate_component,
)
from .pyproject import find_pyproject, load_emergenv_config, plan_builds

# Suffixes stripped from a build <target> for shell-completion convenience.
TARGET_SUFFIXES = (
    ".local.emerg.age",
    ".local.emerg.env",
    ".emerg.age",
    ".emerg.env",
    ".age",
    ".env",
)

GITIGNORE_CONTENT = (
    "# Plain-text environment files must never be committed -\n"
    "# commit the encrypted .age files instead.\n"
    "*.env\n"
)

# Public keys seeded into a fresh authorized_keys, in preference order.
SSH_PUBLIC_KEYS = ("id_ed25519.pub", "id_rsa.pub")


def error(message: str) -> None:
    """Report an error to stderr. Always emitted; never silenced."""
    print(f"error: {message}", file=sys.stderr)


def _collect_ssh_public_keys() -> list[str]:
    """Return the contents of the user's SSH public keys that exist."""
    ssh_dir = Path.home() / ".ssh"
    keys = []
    for name in SSH_PUBLIC_KEYS:
        path = ssh_dir / name
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                keys.append(content)
    return keys


def _resolve_editor() -> list[str] | None:
    """Return the editor command (argv) to use, or None if none is runnable."""
    candidates: list[list[str]] = []
    for var in ("VISUAL", "EDITOR"):
        value = os.environ.get(var)
        if value:
            candidates.append(shlex.split(value))
    candidates.append(["/usr/bin/editor"])
    candidates.append(["vi"])

    for argv in candidates:
        program = argv[0]
        if os.path.isabs(program):
            if os.path.isfile(program) and os.access(program, os.X_OK):
                return argv
        elif shutil.which(program):
            return argv
    return None


def _env_for_age(age_path: Path) -> Path:
    """The sibling ``.env`` path for a given ``.age`` path."""
    return env_file(age_path.with_name(age_path.name[: -len(AGE_SUFFIX)]))


def _age_for_env(env_path: Path) -> Path:
    """The sibling ``.age`` path for a given ``.env`` path."""
    return age_file(env_path.with_name(env_path.name[: -len(ENV_SUFFIX)]))


def _write_private(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` as an owner-only (0o600) plaintext file.

    Used only for the plaintext ``.env`` files emergenv produces (decrypted
    fragments and built outputs), which hold real secrets and are *not* committed.
    A plain ``write_bytes`` would create the file under the process umask (usually
    world- or group-readable); instead we open with mode 0o600 and ``fchmod`` the
    descriptor to 0o600 *before* writing the bytes - so a freshly created file is
    private from the outset, and overwriting a pre-existing looser file tightens
    it before any secret content lands in it.

    Committed artefacts (``.age`` ciphertext, ``authorized_keys``, ``.gitignore``)
    and the data directory are deliberately left at standard permissions: git
    does not record file modes beyond the executable bit, so it would reset them
    to the umask default on the next checkout anyway - restricting them here would
    only create a false sense of security.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(data)


def _write_encrypted(plaintext: bytes, age: Path) -> bool:
    """Encrypt ``plaintext`` to ``age``, skipping the write if it is unchanged.

    If ``age`` already decrypts to ``plaintext``, the file is left untouched.
    ``age`` ciphertext is non-deterministic, so re-encrypting identical plaintext
    would otherwise rewrite the file every time - churning git history and the
    file's modification time for no real change. Returns True if written.
    """
    if age.is_file() and decrypt_bytes(age.read_bytes()) == plaintext:
        return False
    age.write_bytes(encrypt_verified(plaintext, age))
    return True


def _decrypt_all() -> None:
    """Decrypt every ``.age`` file to its sibling ``.env``, overwriting it."""
    targets = [pair["age"] for pair in iter_pairs() if pair["age"]]
    if not targets:
        log("no .age files to decrypt")
    for age in targets:
        env = _env_for_age(age)
        log(f"decrypting {age} -> {env}")
        _write_private(env, decrypt_bytes(age.read_bytes()))


def _encrypt_all(keep: bool) -> None:
    """Encrypt every ``.env`` file to its sibling ``.age`` (unchanged skipped)."""
    targets = [pair["env"] for pair in iter_pairs() if pair["env"]]
    if not targets:
        log("no .env files to encrypt")
    for env in targets:
        age = _age_for_env(env)
        if _write_encrypted(env.read_bytes(), age):
            log(f"encrypted {env} -> {age}")
        else:
            log(f"{age} unchanged")
        if not keep:
            env.unlink()
            log(f"removed {env}")


def _require_target(all_flag: bool, name: str | None) -> None:
    """Validate the mutually exclusive ``<fragment>`` / ``--all`` selection."""
    if all_flag and name:
        raise EmergenvError("give a <fragment> or --all, not both")
    if not all_flag and not name:
        raise EmergenvError("give a <fragment> or --all")


def _normalize_target(arg: str) -> str:
    """Strip a build/output extension from a ``<target>`` for convenience."""
    target = arg.strip()
    for suffix in TARGET_SUFFIXES:
        if target.endswith(suffix):
            return target[: -len(suffix)]
    return target


def _parse_components(specs: list[str] | None, *, kind: str) -> list[str]:
    """Flatten and validate repeated, comma-separated CLI values (``--profile``/``--group``)."""
    items = []
    for spec in specs or []:
        for raw in spec.split(","):
            item = raw.strip()
            if not item:
                continue
            if "/" in item:
                raise EmergenvError(f"a {kind} must not contain '/': {item!r}")
            validate_component(item, kind=kind)
            items.append(item)
    return items


def _parse_profiles(specs: list[str] | None) -> list[str]:
    """Flatten and validate ``--profile`` values (repeated and/or comma-separated)."""
    return _parse_components(specs, kind="profile")


def _trace_override(args: argparse.Namespace) -> str | list[str] | None:
    """The trace value from ``--trace``/``--trace-all`` for the pyproject runner."""
    if args.trace_all:
        return "*"
    if not args.trace:
        return None
    variables: list[str] = []
    for spec in args.trace:
        for raw in spec.split(","):
            item = raw.strip()
            if item == "*":
                return "*"
            if item:
                variables.append(item)
    return variables or None


def cmd_init(_args: argparse.Namespace) -> int:
    """Create the ``emergenv/`` scaffold in the current directory."""
    base = Path(DATA_DIR_NAME)
    try:
        base.mkdir()
    except FileExistsError:
        raise EmergenvError(f"'{base}/' already exists")

    # The directory and the files below are committed (or, for the directory,
    # recreated by a checkout). git records no permissions beyond the executable
    # bit, so it resets these to the umask default anyway - we leave them standard
    # rather than pretend to protect them. Only the uncommitted plaintext .env
    # files emergenv writes get owner-only permissions (see _write_private).
    gitignore = base / ".gitignore"
    gitignore.write_text(GITIGNORE_CONTENT, encoding="utf-8")

    authorized_keys = base / "authorized_keys"
    keys = _collect_ssh_public_keys()
    authorized_keys.write_text("".join(f"{key}\n" for key in keys), encoding="utf-8")

    key_note = f"{len(keys)} SSH public key(s)" if keys else "no SSH public keys found"
    log(f"initialised {base}/ (.gitignore, authorized_keys with {key_note})")
    return 0


def _edit_all() -> int:
    """Decrypt every fragment, wait for ENTER, then re-encrypt every fragment.

    Refuses unless ``status`` is all-green (only encrypted files exist). That
    gate is doing double duty: status's recipient pre-flight also guarantees you
    can decrypt every fragment now and re-encrypt it afterwards, and all-green
    means there is no stray ``.env`` to clobber. Only the fragments you actually
    change get a new ``.age`` (unchanged ones are skipped). Do not touch the
    ``.age`` files or run ``git`` while editing. If re-encryption fails partway
    your edits survive as ``.env`` files; fix the cause and run ``encrypt --all``
    and/or ``clean``.
    """
    if cmd_status(argparse.Namespace()) != 0:
        raise EmergenvError(
            "edit --all requires an all-green status (only encrypted files "
            "exist); resolve the issues listed above, then retry"
        )
    log("")
    _decrypt_all()
    input("\nedit the decrypted .env files now, then press ENTER to re-encrypt...")
    print()
    _encrypt_all(keep=False)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    """Decrypt a fragment, open it in an editor, then re-encrypt it."""
    require_data_dir()
    _require_target(args.all, args.name)
    if args.all:
        return _edit_all()

    base = resolve_name(args.name)
    age, env = age_file(base), env_file(base)

    if not age.is_file():
        raise EmergenvError(f"{age} does not exist")
    if env.exists():
        raise EmergenvError(f"{env} already exists")

    log(f"decrypting {age} -> {env}")
    _write_private(env, decrypt_bytes(age.read_bytes()))

    editor = None if args.wait else _resolve_editor()
    if editor is None:
        if not args.wait:
            log("no usable editor found ($VISUAL/$EDITOR/editor/vi)")
        input(f"\nedit {env} now, then press ENTER to re-encrypt...")
        print()
    else:
        log(f"opening {env} with {editor[0]}")
        subprocess.run([*editor, str(env)])

    if _write_encrypted(env.read_bytes(), age):
        log(f"encrypted {env} -> {age}")
    else:
        log(f"{age} unchanged")
    env.unlink()
    log(f"removed {env}")
    return 0


def cmd_decrypt(args: argparse.Namespace) -> int:
    """Decrypt one fragment, or every ``.age`` file with ``--all``."""
    require_data_dir()
    _require_target(args.all, args.name)

    if args.all:
        _decrypt_all()
        return 0

    base = resolve_name(args.name)
    age, env = age_file(base), env_file(base)
    if not age.is_file():
        raise EmergenvError(f"{age} does not exist")
    if env.exists():
        raise EmergenvError(f"{env} already exists")
    log(f"decrypting {age} -> {env}")
    _write_private(env, decrypt_bytes(age.read_bytes()))
    return 0


def cmd_encrypt(args: argparse.Namespace) -> int:
    """Encrypt one fragment, or every ``.env`` file with ``--all``."""
    require_data_dir()
    _require_target(args.all, args.name)

    if args.all:
        _encrypt_all(args.keep)
        return 0

    base = resolve_name(args.name)
    age, env = age_file(base), env_file(base)
    if not env.is_file():
        raise EmergenvError(f"{env} does not exist")
    if _write_encrypted(env.read_bytes(), age):
        log(f"encrypted {env} -> {age}")
    else:
        log(f"{age} unchanged")
    if not args.keep:
        env.unlink()
        log(f"removed {env}")
    return 0


def cmd_rekey(_args: argparse.Namespace) -> int:
    """Re-encrypt every fragment to its current recipients (after key changes).

    Run this after editing any ``authorized_keys``. Unlike ``encrypt --all`` it
    *always* rewrites each ``.age``: the plaintext is unchanged but the recipient
    set is not, and ``age`` ciphertext doesn't expose its recipients for the usual
    unchanged-skip to compare against.

    Two in-memory passes, so a *detectable* problem aborts before anything is
    written:

    1. Decrypt every ``.age`` in memory (you must be a recipient of all of them).
       If a plaintext ``.env`` sits beside an ``.age`` and they differ, we cannot
       know which is authoritative, so we abort and list the offenders. A fragment
       that is ``.env``-only is adopted: its ``.age`` will be created.
    2. Re-encrypt every collected plaintext to its nearest ``authorized_keys``,
       overwriting the ``.age``.

    ``.env`` files are never created, written, or removed (run ``clean`` afterwards
    to drop any plaintext). Pass 2 is not atomic: a failure partway can leave the
    tree partly rekeyed - fix the cause and run again.
    """
    require_data_dir()

    plans: list[tuple[bytes, Path]] = []  # (plaintext, .age destination)
    mismatched: list[str] = []
    for pair in iter_pairs():
        age, env = pair["age"], pair["env"]
        if age is not None:
            plaintext = decrypt_bytes(age.read_bytes())  # raises -> nothing written
            if env is not None and env.read_bytes() != plaintext:
                mismatched.append(pair["name"])
                continue
            plans.append((plaintext, age))
        else:  # .env-only fragment: adopt it, creating its .age
            plans.append((env.read_bytes(), _age_for_env(env)))

    if mismatched:
        raise EmergenvError(
            "rekey aborted - a plaintext .env differs from its .age for: "
            + ", ".join(mismatched)
            + " (resolve with encrypt/decrypt/clean, then retry)"
        )
    if not plans:
        log("nothing to rekey")
        return 0

    for plaintext, age in plans:
        age.write_bytes(encrypt_verified(plaintext, age))
        log(f"rekeyed {age}")
    return 0


def _status_of(pair: dict) -> tuple[int, str, str]:
    """Classify a fragment into ``(exit_code, label, colour)``."""
    age, env = pair["age"], pair["env"]
    if age and env:
        try:
            matches = decrypt_bytes(age.read_bytes()) == env.read_bytes()
        except EmergenvError:
            return 3, "ERROR", "red"
        if matches:
            return 1, "AGE+ENV MATCH", "orange"
        return 2, "AGE+ENV MISMATCH", "red"
    if age:
        return 0, "AGE", "green"
    return 1, "ENV", "orange"


def cmd_status(_args: argparse.Namespace) -> int:
    """List every fragment with its state; exit code is the worst state seen.

    First, as a pre-flight, it verifies you are a recipient of every
    ``authorized_keys`` set; if not it reports which and aborts with code 3
    (you would otherwise only discover the lockout when a write fails).

    Per file: 0 ``AGE`` (encrypted only), 1 ``AGE+ENV MATCH`` / ``ENV``,
    2 ``AGE+ENV MISMATCH``, 3 ``ERROR`` (undecryptable). Every name is listed
    even if some fail to decrypt; the return value is the highest code seen.
    """
    require_data_dir()

    # Pre-flight: you must be a recipient of every authorized_keys set, or you
    # would be unable to encrypt/edit/rekey files governed by the ones you miss
    # (which only ever fails at write time, per file). Surface it up front.
    gaps = inaccessible_authorized_keys()
    if gaps:
        base = data_dir()
        listed = ", ".join(p.relative_to(base).as_posix() for p in gaps)
        error(
            "you are not a recipient of these authorized_keys sets, so you could "
            f"not encrypt/edit/rekey files governed by them: {listed}"
        )
        return 3

    pairs = iter_pairs()
    if not pairs:
        log("no files found")
        return 0
    worst = 0
    for pair in pairs:
        code, label, colour = _status_of(pair)
        worst = max(worst, code)
        log(f"{pair['name']} {colourize(label, colour)}")
    return worst


def cmd_clean(_args: argparse.Namespace) -> int:
    """Delete each ``.env`` whose ``.age`` decrypts to identical contents."""
    require_data_dir()
    removed = 0
    for pair in iter_pairs():
        if not (pair["age"] and pair["env"]):
            continue
        if decrypt_bytes(pair["age"].read_bytes()) == pair["env"].read_bytes():
            pair["env"].unlink()
            log(f"removed {pair['env']} (matches {pair['age']})")
            removed += 1
        else:
            log(f"kept {pair['env']} (differs from {pair['age']})")
    if removed == 0:
        log("nothing to clean")
    return 0


def _parse_trace(
    values: list[str] | None, trace_all: bool
) -> tuple[bool, list[str]] | None:
    """Parse ``--trace`` / ``--trace-all`` flags into a (all_flag, names) pair.

    Returns ``None`` when tracing is not requested at all.  Returns
    ``(True, [])`` when every variable should be traced, or
    ``(False, names)`` for a specific subset.
    """
    if not values and not trace_all:
        return None
    names: list[str] = []
    for chunk in values or []:
        names += [p for p in chunk.split(",") if p]
    if trace_all or "*" in names:
        return (True, [])
    return (False, names)


def cmd_build(args: argparse.Namespace) -> int:
    """Build ``<target>.env`` from ``<target>.emerg.*`` (and ``.local``)."""
    require_data_dir()
    target = _normalize_target(args.target)
    validate_component(target, kind="target")
    profiles = _parse_profiles(args.profile)

    to_stdout = args.output == "-"
    if to_stdout:
        # the build content goes to stdout, so drop our normal logs entirely;
        # stderr is left for errors only.
        output.log_mode = output.LOG_OFF

    log(f"building: {target}")
    if profiles:
        log(f"profiles: {' '.join(profiles)}")
    log(f"emergenv: {data_dir()}")

    trace = _parse_trace(
        getattr(args, "trace", None), getattr(args, "trace_all", False)
    )

    build_kwargs = dict(
        mark_source=not args.no_source,
        bare=args.bare,
        local=not args.no_local,
        verbose=args.verbose,
        no_filter=args.no_filter,
    )

    builder: _Builder | None = None
    trace_wanted: list[str] | None = None
    if trace is None:
        # build_target logs the resolved base, local, and each imported fragment.
        text = build_target(target, profiles, **build_kwargs)
    else:
        text, builder = build_with_trace(target, profiles, **build_kwargs)
        # Validate requested variables once, before any output, so an unknown
        # name fails cleanly on stderr without writing partial stdout.
        all_keys = builder.trace_keys()
        trace_wanted = all_keys if trace[0] else trace[1]
        missing = [k for k in trace_wanted if k not in all_keys]
        if missing:
            raise EmergenvError(f"variable {missing[0]!r} not in build output")

    if to_stdout:
        sys.stdout.write(text)
        # the trace is intentionally dropped here (log_mode == LOG_OFF).
        return 0

    if args.output:
        out = Path(args.output)
    else:
        out = Path(".env") if target == "dot" else Path(f"{target}.env")
    log(f"writing: {out.resolve()}")
    try:
        _write_private(out, text.encode("utf-8"))
    except OSError as exc:
        raise EmergenvError(f"cannot write {out}: {exc.strerror or exc}")

    if builder is not None and trace_wanted is not None:
        log(builder.trace_text(trace_wanted).rstrip("\n"))

    return 0


def cmd_pyproject(args: argparse.Namespace) -> int:
    """Run the build chain declared in ``pyproject.toml``'s ``[tool.emergenv]``.

    Resolves the config into a list of ``emergenv build`` commands and shells out
    to each (from the pyproject's directory), stopping on the first failure.
    ``--dry-run`` prints the commands without running them.
    """
    try:
        import tomllib  # noqa: F401  - Python 3.11+; presence is the gate
    except ModuleNotFoundError:
        raise EmergenvError(
            "the 'pyproject' command requires Python 3.11+ (for the standard-"
            "library tomllib parser). Run 'emergenv build' per target instead."
        )

    pyproject = find_pyproject(Path.cwd())
    config = load_emergenv_config(pyproject)

    overrides = {
        "bare": args.bare,
        "local": args.local,
        "source": args.source,
        "filter": args.filter,
        "verbose": args.verbose,
        "trace": _trace_override(args),
    }
    commands = plan_builds(
        config,
        _parse_profiles(args.profile),
        _parse_components(args.group, kind="group"),
        overrides,
    )

    if shutil.which("emergenv") is None:
        raise EmergenvError(
            "emergenv not found on PATH - the pyproject runner shells out to "
            "'emergenv build'"
        )

    project_dir = str(pyproject.parent)
    log(f"pyproject: {pyproject}")
    for argv in commands:
        log("→ " + shlex.join(argv))
        if args.dry_run:
            continue
        result = subprocess.run(argv, cwd=project_dir)
        if result.returncode != 0:
            raise EmergenvError(
                f"build failed (exit {result.returncode}): {shlex.join(argv)}"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="emergenv",
        description="Encrypted, Merged Environment. "
        "A <fragment> is a path under emergenv/ without extension; a leading "
        "'emergenv/' and a trailing .age/.env are stripped for convenience.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser(
        "init", help="create the emergenv/ scaffold (.gitignore, authorized_keys)"
    )
    p_init.set_defaults(func=cmd_init)

    p_edit = subparsers.add_parser(
        "edit", help="decrypt a fragment, open it in an editor, then re-encrypt it"
    )
    p_edit.add_argument(
        "name",
        metavar="<fragment>",
        nargs="?",
        help="fragment to edit (its .age must exist)",
    )
    p_edit.add_argument(
        "--all",
        action="store_true",
        help="edit every fragment: decrypt all, wait for ENTER, re-encrypt all "
        "(requires an all-green status)",
    )
    p_edit.add_argument(
        "--wait",
        action="store_true",
        help="skip launching an editor; wait for ENTER instead (edit elsewhere)",
    )
    p_edit.set_defaults(func=cmd_edit)

    p_decrypt = subparsers.add_parser(
        "decrypt", help="decrypt a fragment's .age to .env"
    )
    p_decrypt.add_argument(
        "name", metavar="<fragment>", nargs="?", help="fragment to decrypt"
    )
    p_decrypt.add_argument(
        "--all",
        action="store_true",
        help="decrypt every .age file, overwriting existing .env files",
    )
    p_decrypt.set_defaults(func=cmd_decrypt)

    p_encrypt = subparsers.add_parser(
        "encrypt", help="encrypt a fragment's .env to .age"
    )
    p_encrypt.add_argument(
        "name", metavar="<fragment>", nargs="?", help="fragment to encrypt"
    )
    p_encrypt.add_argument(
        "--all",
        action="store_true",
        help="encrypt every .env file, overwriting existing .age files",
    )
    p_encrypt.add_argument(
        "--keep",
        action="store_true",
        help="keep the .env file(s) instead of deleting after encryption",
    )
    p_encrypt.set_defaults(func=cmd_encrypt)

    p_status = subparsers.add_parser(
        "status", help="list names, their formats, and age/env match state"
    )
    p_status.set_defaults(func=cmd_status)

    p_clean = subparsers.add_parser(
        "clean", help="delete .env files whose .age has identical contents"
    )
    p_clean.set_defaults(func=cmd_clean)

    p_rekey = subparsers.add_parser(
        "rekey",
        help="re-encrypt every fragment to current recipients "
        "(run after editing authorized_keys)",
    )
    p_rekey.set_defaults(func=cmd_rekey)

    p_build = subparsers.add_parser(
        "build", help="build <target>.env from <target>.emerg.* (and .local)"
    )
    p_build.add_argument(
        "target", metavar="<target>", help="target to build ('dot' produces .env)"
    )
    p_build.add_argument(
        "--profile",
        action="append",
        help="comma-separated profiles, applied in order",
    )
    p_build.add_argument(
        "--no-source",
        action="store_true",
        help="omit provenance comments ('# FROM: <path>' and '# COMPUTED:')",
    )
    p_build.add_argument(
        "--bare",
        action="store_true",
        help="strip all comments and blank lines, leaving only assignments",
    )
    p_build.add_argument(
        "--no-local",
        action="store_true",
        help="ignore the <target>.local.emerg.env override",
    )
    p_build.add_argument(
        "--output",
        metavar="<filename>",
        help="write to this file instead of the default; '-' writes to stdout",
    )
    p_build.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show every search path tried (both .age/.env), colour-coded",
    )
    p_build.add_argument(
        "--trace",
        metavar="<vars>",
        action="append",
        help="trace the assignment history of variables (comma-separated, "
        "repeatable; '*' for all). Suppressed under --output -",
    )
    p_build.add_argument(
        "--trace-all",
        action="store_true",
        help="trace every variable in the output (same as --trace '*')",
    )
    p_build.add_argument(
        "--no-filter",
        action="store_true",
        help="ignore @filter directives (emit every key)",
    )
    p_build.set_defaults(func=cmd_build)

    p_pyproject = subparsers.add_parser(
        "pyproject",
        help="run the build chain declared in pyproject.toml ([tool.emergenv])",
    )
    p_pyproject.add_argument(
        "--profile",
        action="append",
        help="comma-separated profiles, applied in order (the per-deploy axis)",
    )
    p_pyproject.add_argument(
        "--group",
        action="append",
        help="only run builds tagged with these group(s); comma-separated",
    )
    for _name, _desc in (
        ("bare", "strip comments and blank lines"),
        ("local", "apply the <target>.local.emerg.env override"),
        ("source", "emit '# FROM:' provenance"),
        ("filter", "apply @filter directives"),
        ("verbose", "show every search path tried"),
    ):
        p_pyproject.add_argument(
            f"--{_name}",
            action=argparse.BooleanOptionalAction,
            default=None,
            help=f"override every build's format: {_desc}",
        )
    p_pyproject.add_argument(
        "--trace",
        metavar="<vars>",
        action="append",
        help="trace these variables across every build (replaces the TOML trace)",
    )
    p_pyproject.add_argument(
        "--trace-all",
        action="store_true",
        help="trace every variable (same as --trace '*')",
    )
    p_pyproject.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resolved commands without running them",
    )
    p_pyproject.set_defaults(func=cmd_pyproject)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Behave like a normal Unix filter when our stdout is piped to a reader that
    # closes early (e.g. `... | head`): let SIGPIPE kill us cleanly (exit 141)
    # rather than raising BrokenPipeError and printing a shutdown traceback.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    parser = build_parser()
    args = parser.parse_args(argv)

    # parse_args above handles --help/--version (they exit here), so the age
    # check only gates the actual subcommands. The pyproject runner is exempt:
    # it only shells out to `emergenv build`, which checks age itself.
    try:
        if args.command != "pyproject" and not age_check():
            raise EmergenvError(
                "the 'age' binary (>= 1.0.0) was not found on your PATH, is not "
                "runnable, or reports an unsupported version. Install it from "
                "https://github.com/FiloSottile/age and try again."
            )
        exit_code: int = args.func(args)
        return exit_code
    except EmergenvError as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
