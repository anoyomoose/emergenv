"""The build engine: expand directives and merge into a final ``.env``.

Everything happens on in-memory, source-tagged lines (:class:`Line`). Encrypted
sources are decrypted in memory via :mod:`emergenv.crypto` and never written back
as plaintext; the only output is the final ``<target>.env`` string returned by
:func:`build_target`.

Two entry shapes share one recursive engine:

- ``build <target>`` starts from a base: ``<target>.emerg.env`` in the working
  directory if present, otherwise ``emergenv/<target>.emerg.(age|env)`` (a CWD
  plaintext base shadows an encrypted one in the store). An optional
  ``<target>.local.emerg.env`` in the working directory is appended.
- ``@include <fragment>`` / ``@<key>=<fragment>`` start from a *resolved fragment* searched
  under ``emergenv/`` (global -> each profile -> target -> target+profile),
  preferring ``.age`` over ``.env``.

Both reduce to: take lines -> expand directives recursively -> evaluate $/%
computed assignments against a live namespace -> apply last-wins once -> render.
Cycles in includes are caught with an ancestor stack; resolved names and
decrypted files are memoised.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import EmergenvError
from .crypto import decrypt_bytes
from .expand import ExpansionError, expand
from .output import log
from .paths import (
    AGE_SUFFIX,
    DATA_DIR_NAME,
    ENV_SUFFIX,
    data_dir,
    validate_component,
    working_dir,
)


@dataclass(frozen=True)
class Line:
    """A single output line tagged with the file it came from."""

    text: str
    source: str


# An assignment: optional leading whitespace, optional ``export ``, then KEY=.
_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")
# Directives (operate on the whole line, after optional leading whitespace).
_INCLUDE = re.compile(r"^\s*@include\s+(\S.*?)\s*$")
_KEYREF = re.compile(r"^\s*(export\s+)?@([A-Za-z_][A-Za-z0-9_]*)=(\S.*?)\s*$")
_LEADING_AT = re.compile(r"^\s*(?:export\s+)?@")
# A computed assignment: optional ``export``, then ``$`` (built keys only) or
# ``%`` (also the environment), KEY, ``=``, and the template to expand.
_COMPUTED = re.compile(
    r"^\s*(?P<export>export\s+)?(?P<marker>[$%])"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<template>.*)$"
)


def _assignment_key(text: str) -> str | None:
    """The variable name if ``text`` is an active assignment - plain or computed."""
    plain = _ASSIGN.match(text)
    if plain:
        return plain.group(1)
    computed = _COMPUTED.match(text)
    return computed.group("key") if computed else None


def _assignment_value(text: str) -> str:
    """The value of an assignment line (everything after the first ``=``)."""
    return text.split("=", 1)[1]


def _parse_directive(text: str) -> tuple | None:
    """Classify a directive line.

    Returns ``("include", name)``, ``("keyref", exported, key, name)``, or
    ``None`` if the line is not a directive. Raises :class:`EmergenvError` for a
    line that looks like a directive but is malformed.
    """
    include = _INCLUDE.match(text)
    if include:
        return ("include", include.group(1).strip())

    keyref = _KEYREF.match(text)
    if keyref:
        return (
            "keyref",
            bool(keyref.group(1)),
            keyref.group(2),
            keyref.group(3).strip(),
        )

    if _LEADING_AT.match(text):
        stripped = text.strip()
        if re.match(r"^\s*export\s+@include\b", text):
            raise EmergenvError(f"cannot 'export' an @include: {stripped!r}")
        if re.match(r"^\s*@include\b", text):
            raise EmergenvError(f"@include is missing a fragment: {stripped!r}")
        raise EmergenvError(f"malformed directive: {stripped!r}")
    return None


def _last_wins(lines: list[Line]) -> list[Line]:
    """Comment out every active assignment that a later one overrides."""
    last_index: dict[str, int] = {}
    for i, line in enumerate(lines):
        key = _assignment_key(line.text)
        if key is not None:
            last_index[key] = i

    result = []
    for i, line in enumerate(lines):
        key = _assignment_key(line.text)
        if key is not None and last_index[key] != i:
            result.append(Line("# " + line.text, line.source))
        else:
            result.append(line)
    return result


def _is_blank_or_comment(text: str) -> bool:
    stripped = text.strip()
    return stripped == "" or stripped.startswith("#")


def _trim_blank_edges(lines: list[Line]) -> list[Line]:
    """Drop leading and trailing blank (whitespace-only) lines."""
    start, end = 0, len(lines)
    while start < end and lines[start].text.strip() == "":
        start += 1
    while end > start and lines[end - 1].text.strip() == "":
        end -= 1
    return lines[start:end]


def _render(lines: list[Line], mark_source: bool, bare: bool) -> str:
    """Render lines to text.

    ``bare`` drops every comment and blank line (and therefore all source
    markers and last-wins-commented overrides), leaving only the winning
    assignments.

    Otherwise, for neat output: runs of blank lines collapse to a single blank,
    leading/trailing blanks are dropped, and a ``# FROM: <source>`` header is
    emitted before the first *content* (non-blank) line of each new source
    (unless ``mark_source`` is false). A source that contributes only a blank
    line therefore gets no header.
    """
    if bare:
        kept = [line.text for line in lines if not _is_blank_or_comment(line.text)]
        return "".join(f"{text}\n" for text in kept)

    out: list[str] = []
    header_source: str | None = None
    pending_blank = False
    for line in lines:
        if line.text.strip() == "":
            pending_blank = bool(out)  # collapse runs; never emit a leading blank
            continue
        if pending_blank:
            out.append("")
            pending_blank = False
        if mark_source and line.source != header_source:
            out.append(f"# FROM: {line.source}")
            header_source = line.source
        out.append(line.text)
    return "\n".join(out) + "\n" if out else ""


class _Builder:
    def __init__(
        self,
        target: str,
        profiles: list[str],
        *,
        mark_source: bool = True,
        bare: bool = False,
        local: bool = True,
    ):
        self.target = target
        self.profiles = profiles
        self.mark_source = mark_source
        self.bare = bare
        self.local = local
        self._resolve_cache: dict[str, list[Line]] = {}
        self._file_cache: dict[object, list[Line]] = {}
        self._stack: list[str] = []

    def build(self) -> str:
        lines = self._read_base()
        expanded = self._expand(lines)
        computed = self._substitute(expanded)
        # _render does the blank-line tidying (collapse runs, trim edges, drop
        # headers for blank-only sources).
        return _render(_last_wins(computed), self.mark_source, self.bare)

    def _read_base(self) -> list[Line]:
        base_path = self._find_base()
        if base_path is None:
            raise EmergenvError(
                f"no base to build: neither {self.target}.emerg.env (here) nor "
                f"{DATA_DIR_NAME}/{self.target}.emerg.(age|env)"
            )
        log(f"target: {base_path}")
        lines = self._read_file(base_path)

        # The .local layer is plaintext-only and always relative to the cwd.
        local_path = working_dir() / f"{self.target}.local.emerg{ENV_SUFFIX}"
        if self.local and local_path.is_file():
            log(f"local: {local_path}")
            lines = lines + self._read_file(local_path)
        return lines

    def _find_base(self) -> Path | None:
        # A plaintext base in the working directory shadows everything (honors
        # '/' in the target name); both anchors yield an absolute path.
        cwd_base = working_dir() / f"{self.target}.emerg{ENV_SUFFIX}"
        if cwd_base.is_file():
            return cwd_base
        # Otherwise fall back to an (optionally encrypted) base in emergenv/.
        for suffix in (AGE_SUFFIX, ENV_SUFFIX):
            path = data_dir() / f"{self.target}.emerg{suffix}"
            if path.is_file():
                return path
        return None

    def _read_file(self, path: Path) -> list[Line]:
        if path in self._file_cache:
            return self._file_cache[path]
        raw = path.read_bytes()
        if path.suffix == AGE_SUFFIX:
            try:
                raw = decrypt_bytes(raw)
            except EmergenvError as exc:
                via = f" (via {' -> '.join(self._stack)})" if self._stack else ""
                raise EmergenvError(
                    f"failed to decrypt {self._source_label(path)}{via}: {exc}"
                ) from exc
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EmergenvError(f"{path} is not valid UTF-8: {exc}") from exc
        source = self._source_label(path)
        # Normalise every file: trim blank lines off both ends, then append a
        # single blank line so concatenated files are separated by exactly one
        # (the final trailing one is dropped in build()).
        lines = _trim_blank_edges([Line(t, source) for t in text.splitlines()])
        lines.append(Line("", source))
        self._file_cache[path] = lines
        return lines

    def _source_label(self, path: Path) -> str:
        # base files sit at the cwd; includes live under the (possibly
        # discovered) emergenv root - try both anchors before giving up.
        for anchor in (working_dir(), data_dir().parent):
            try:
                return str(path.relative_to(anchor))
            except ValueError:
                continue
        return str(path)

    def _expand(self, lines: list[Line]) -> list[Line]:
        out: list[Line] = []
        for line in lines:
            directive = _parse_directive(line.text)
            if directive is None:
                out.append(line)
            elif directive[0] == "include":
                out.extend(self._resolve(directive[1]))
            else:
                _, exported, key, name = directive
                win = self._winning_line(name, key)
                prefix = "export " if exported else ""
                computed = _COMPUTED.match(win.text)
                if computed is not None:
                    text = (
                        f"{prefix}{computed.group('marker')}{key}"
                        f"={computed.group('template')}"
                    )
                else:
                    text = f"{prefix}{key}={_assignment_value(win.text)}"
                out.append(Line(text, win.source))
        return out

    def _resolve(self, name: str) -> list[Line]:
        if name in self._resolve_cache:
            return self._resolve_cache[name]
        if name in self._stack:
            chain = " -> ".join([*self._stack, name])
            raise EmergenvError(f"include cycle: {chain}")
        self._stack.append(name)
        block: list[Line] = []
        for path in self._search(name):  # only files that exist are returned
            log(f"importing: {path.relative_to(data_dir()).as_posix()}")
            block.extend(self._expand(self._read_file(path)))
        self._stack.pop()
        self._resolve_cache[name] = block
        return block

    def _winning_line(self, name: str, key: str) -> Line:
        found = None
        for line in self._resolve(name):
            if _assignment_key(line.text) == key:
                found = line
        if found is None:
            raise EmergenvError(f"key {key!r} not found in {name!r}")
        return found

    def _substitute(self, lines: list[Line]) -> list[Line]:
        """Evaluate ``$KEY=`` / ``%KEY=`` lines against a live namespace.

        A single pass, top to bottom: every assignment (plain, keyref-resolved,
        or computed) updates the namespace as it is read, so a computed line sees
        the values defined *above* it (define-before-use; cycles are impossible).
        ``$`` sees built keys only; ``%`` also sees the environment, with built
        keys winning. A computed line renders as a ``# COMPUTED:`` comment of the
        original directive followed by the resolved assignment.
        """
        namespace: dict[str, str] = {}
        out: list[Line] = []
        for line in lines:
            computed = _COMPUTED.match(line.text)
            if computed is not None:
                marker = computed.group("marker")
                key = computed.group("key")
                template = computed.group("template")
                lookup = {**os.environ, **namespace} if marker == "%" else namespace
                try:
                    value = expand(template, lookup)
                except ExpansionError as exc:
                    raise EmergenvError(
                        f"failed to expand {key} (in {line.source}): {exc}"
                    ) from exc
                namespace[key] = value
                prefix = "export " if computed.group("export") else ""
                out.append(Line("# COMPUTED: " + line.text.strip(), line.source))
                out.append(Line(f"{prefix}{key}={value}", line.source))
                continue
            key = _assignment_key(line.text)
            if key is not None:
                namespace[key] = _assignment_value(line.text)
            out.append(line)
        return out

    def _search(self, name: str) -> list:
        bang = name.startswith("!")
        core = name[1:] if bang else name
        validate_component(core, kind="fragment")

        base = data_dir()
        if bang:
            stems = [base / core]
        else:
            stems = [base / core]
            stems += [base / profile / core for profile in self.profiles]
            stems.append(base / self.target / core)
            stems += [base / self.target / profile / core for profile in self.profiles]

        root = base.resolve()
        found = []
        for stem in stems:
            age = stem.with_name(stem.name + AGE_SUFFIX)
            env = stem.with_name(stem.name + ENV_SUFFIX)
            chosen = age if age.is_file() else env if env.is_file() else None
            if chosen is None:
                continue
            # Symlinks are allowed, but must stay inside emergenv/.
            if root not in chosen.resolve().parents:
                raise EmergenvError(
                    f"{name!r} resolves outside '{DATA_DIR_NAME}/' via {chosen}"
                )
            found.append(chosen)

        if not found:
            raise EmergenvError(f"@include {name!r} matched no file")
        return found


def build_target(
    target: str,
    profiles: list[str],
    *,
    mark_source: bool = True,
    bare: bool = False,
    local: bool = True,
) -> str:
    """Build ``<target>`` into the final ``.env`` text (see module docstring)."""
    return _Builder(
        target, profiles, mark_source=mark_source, bare=bare, local=local
    ).build()
