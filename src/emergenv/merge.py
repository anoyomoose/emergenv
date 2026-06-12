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
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from . import EmergenvError
from .crypto import decrypt_bytes
from .expand import ExpansionError, expand
from .output import colourize, log
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


@dataclass
class _TraceNode:
    """A node in a variable's provenance tree."""

    kind: str  # "root" | "directive" | "assign"
    directive: str | None = None  # for kind == "directive"
    key: str | None = None  # for kind == "assign"
    source: str | None = None  # file label, for kind == "assign"
    text: str | None = None  # literal assignment line
    ignored: bool = False  # keyref candidate that lost the inner selection
    value: str | None = None  # resolved value (filled after substitution)
    children: list["_TraceNode"] = field(default_factory=list)


class _Trace:
    """Records a provenance tree as the builder expands directives."""

    def __init__(self) -> None:
        self.root = _TraceNode(kind="root")
        self._stack: list[_TraceNode] = [self.root]

    def enter(self, directive: str) -> None:
        node = _TraceNode(kind="directive", directive=directive)
        self._stack[-1].children.append(node)
        self._stack.append(node)

    def leave(self) -> None:
        self._stack.pop()

    def assign(
        self, key: str, source: str, text: str, *, ignored: bool = False
    ) -> None:
        self._stack[-1].children.append(
            _TraceNode(
                kind="assign", key=key, source=source, text=text, ignored=ignored
            )
        )


@dataclass(frozen=True)
class _Stem:
    """One search location for a fragment, with both variant paths."""

    rel: str  # path relative to emergenv/, posix, without suffix
    age: Path
    env: Path
    age_exists: bool
    env_exists: bool

    @property
    def chosen(self) -> Path | None:
        if self.age_exists:
            return self.age
        if self.env_exists:
            return self.env
        return None


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
        verbose: bool = False,
        trace: bool = False,
    ):
        self.target = target
        self.profiles = profiles
        self.mark_source = mark_source
        self.bare = bare
        self.local = local
        self.verbose = verbose
        self._trace: _Trace | None = _Trace() if trace else None
        self._trace_final: dict[str, str] = {}
        self._trace_order: list[str] = []
        self._local_label: str | None = None
        self._resolve_cache: dict[str, list[Line]] = {}
        self._file_cache: dict[object, list[Line]] = {}
        self._stack: list[str] = []

    def build(self) -> str:
        lines = self._read_base()
        expanded = self._expand(lines)
        computed = self._substitute(expanded)
        if self._trace is not None:
            self._fill_trace_values(computed)
        # _render does the blank-line tidying (collapse runs, trim edges, drop
        # headers for blank-only sources).
        return _render(_last_wins(computed), self.mark_source, self.bare)

    def _log_base_breakdown(self, cwd_env: Path, em_age: Path, em_env: Path) -> None:
        log(f"target: {self.target}")
        cwd, age, env = cwd_env.is_file(), em_age.is_file(), em_env.is_file()
        if cwd and (age or env):  # accident: cwd shadows an emergenv/ base
            log(
                colourize(
                    f"  - using: {self._source_label(cwd_env)} [ambiguous]", "red"
                )
            )
            for path, exists in ((em_age, age), (em_env, env)):
                if exists:
                    log(
                        colourize(
                            f"  - ignoring: {self._source_label(path)} [ambiguous]",
                            "red",
                        )
                    )
                else:
                    log(colourize(f"  - missing: {self._source_label(path)}", "green"))
            return
        if cwd:  # cwd base, no emergenv/ base
            log(colourize(f"  - using: {self._source_label(cwd_env)} [cwd]", "green"))
            log(
                colourize(
                    f"  - missing: {DATA_DIR_NAME}/{self.target}.emerg.(age|env)",
                    "green",
                )
            )
            return
        # cwd missing: standard age/env table on the emergenv/ slot
        log(colourize(f"  - missing: {self._source_label(cwd_env)} [cwd]", "green"))
        rel = f"{DATA_DIR_NAME}/{self.target}.emerg"
        em_stem = _Stem(rel, em_age, em_env, age, env)
        for line in self._stem_lines(em_stem):
            log(line)

    def _read_base(self) -> list[Line]:
        cwd_env = working_dir() / f"{self.target}.emerg{ENV_SUFFIX}"
        em_age = data_dir() / f"{self.target}.emerg{AGE_SUFFIX}"
        em_env = data_dir() / f"{self.target}.emerg{ENV_SUFFIX}"

        base_path = self._find_base()
        if base_path is None:
            raise EmergenvError(
                f"no base to build: neither {self.target}.emerg.env (here) nor "
                f"{DATA_DIR_NAME}/{self.target}.emerg.(age|env)"
            )
        if self.verbose:
            self._log_base_breakdown(cwd_env, em_age, em_env)
        else:
            log(f"target: {base_path}")
        lines = self._read_file(base_path)

        # The .local layer is plaintext-only and always relative to the cwd.
        local_path = working_dir() / f"{self.target}.local.emerg{ENV_SUFFIX}"
        self._local_label = self._source_label(local_path)
        if not self.local:
            if self.verbose:
                log(
                    colourize(f"local: skipped {local_path.name} [--no-local]", "green")
                )
        elif local_path.is_file():
            if self.verbose:
                log(colourize(f"local: using {local_path}", "green"))
            else:
                log(f"local: {local_path}")
            lines = lines + self._read_file(local_path)
        elif self.verbose:
            log(colourize(f"local: missing {local_path}", "green"))
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
                if self._trace is not None:
                    key = _assignment_key(line.text)
                    if key is not None:
                        self._trace.assign(key, line.source, line.text)
            elif directive[0] == "include":
                if self._trace is not None:
                    self._trace.enter(f"@include {directive[1]}")
                out.extend(self._resolve(directive[1]))
                if self._trace is not None:
                    self._trace.leave()
            else:
                _, exported, ref_key, name = directive
                assert isinstance(ref_key, str)
                prefix = "export " if exported else ""
                if self._trace is None:
                    win = self._winning_line(name, ref_key)
                else:
                    self._trace.enter(f"{prefix}@{ref_key}={name}")
                    candidates = self._winning_lines_silent(name, ref_key)
                    for cand in candidates[:-1]:
                        self._trace.assign(
                            ref_key, cand.source, cand.text, ignored=True
                        )
                    win = candidates[-1]
                computed = _COMPUTED.match(win.text)
                if computed is not None:
                    text = (
                        f"{prefix}{computed.group('marker')}{ref_key}"
                        f"={computed.group('template')}"
                    )
                else:
                    text = f"{prefix}{ref_key}={_assignment_value(win.text)}"
                if self._trace is not None:
                    self._trace.assign(ref_key, win.source, text)
                    self._trace.leave()
                out.append(Line(text, win.source))
        return out

    def _winning_lines_silent(self, name: str, key: str) -> list[Line]:
        """Resolve a keyref fragment without recording its sub-expansion into
        the trace - we record only the matching candidates, not the whole
        fragment. Suspends ``self._trace`` for the duration."""
        saved, self._trace = self._trace, None  # suspend recording during resolve
        try:
            return self._winning_lines(name, key)
        finally:
            self._trace = saved

    def _stem_lines(self, stem: _Stem) -> list[str]:
        """Colour-coded breakdown lines for one age/env location."""
        if not stem.age_exists and not stem.env_exists:
            return [colourize(f"  - missing: {stem.rel}.(age|env)", "green")]

        age_path = f"{stem.rel}{AGE_SUFFIX}"
        env_path = f"{stem.rel}{ENV_SUFFIX}"
        lines: list[str] = []
        if stem.age_exists and stem.env_exists:
            try:
                match = decrypt_bytes(stem.age.read_bytes()) == stem.env.read_bytes()
            except EmergenvError:
                return [
                    colourize(f"  - using: {age_path} [undecryptable]", "red"),
                    colourize(
                        f"  - ignoring: {env_path} [age-preferred,undecryptable]", "red"
                    ),
                ]
            lines.append(colourize(f"  - using: {age_path}", "green"))
            tag = "match" if match else "mismatch"
            match_colour = "orange" if match else "red"
            lines.append(
                colourize(
                    f"  - ignoring: {env_path} [age-preferred,{tag}]", match_colour
                )
            )
        elif stem.age_exists:  # env missing
            lines.append(colourize(f"  - using: {age_path}", "green"))
            lines.append(colourize(f"  - missing: {env_path}", "green"))
        else:  # env exists, age missing
            lines.append(colourize(f"  - missing: {age_path} [env-present]", "red"))
            lines.append(colourize(f"  - using: {env_path} [age-missing]", "orange"))
        return lines

    def _log_fragment_breakdown(self, name: str, records: list[_Stem]) -> None:
        log(f"importing: {name}")
        for stem in records:
            for line in self._stem_lines(stem):
                log(line)

    def _resolve(self, name: str) -> list[Line]:
        if self._trace is None and name in self._resolve_cache:
            return self._resolve_cache[name]
        if name in self._stack:
            chain = " -> ".join([*self._stack, name])
            raise EmergenvError(f"include cycle: {chain}")
        self._stack.append(name)
        records = self._search_records(name)
        found = [r.chosen for r in records if r.chosen is not None]
        # Print the breakdown before the no-match raise, so the user sees every
        # path that was tried even when the fragment resolves to nothing.
        if self.verbose:
            self._log_fragment_breakdown(name, records)
        if not found:
            raise EmergenvError(f"@include {name!r} matched no file")
        block: list[Line] = []
        for path in found:
            if not self.verbose:
                log(f"importing: {path.relative_to(data_dir()).as_posix()}")
            block.extend(self._expand(self._read_file(path)))
        self._stack.pop()
        if self._trace is None:
            self._resolve_cache[name] = block
        return block

    def _winning_lines(self, name: str, key: str) -> list[Line]:
        matches = [
            line for line in self._resolve(name) if _assignment_key(line.text) == key
        ]
        if not matches:
            raise EmergenvError(f"key {key!r} not found in {name!r}")
        return matches

    def _winning_line(self, name: str, key: str) -> Line:
        return self._winning_lines(name, key)[-1]

    def _iter_assign_nodes(self, node: _TraceNode) -> Iterator[_TraceNode]:
        """Non-ignored assign nodes in document (DFS pre-order) order."""
        for child in node.children:
            if child.kind == "assign":
                if not child.ignored:
                    yield child
            else:
                yield from self._iter_assign_nodes(child)

    def _fill_trace_values(self, computed: list[Line]) -> None:
        assert self._trace is not None
        emitted = [c for c in computed if _assignment_key(c.text) is not None]
        nodes = list(self._iter_assign_nodes(self._trace.root))
        if len(nodes) != len(emitted):  # invariant guard - should never happen
            raise EmergenvError(
                "internal trace error: "
                f"{len(nodes)} assign nodes vs {len(emitted)} emitted lines"
            )
        for node, line in zip(nodes, emitted):
            node.value = _assignment_value(line.text)
        self._trace_final = {}
        self._trace_order = []
        for line in emitted:
            key = _assignment_key(line.text)
            assert key is not None
            self._trace_final[key] = _assignment_value(line.text)
            if key not in self._trace_order:
                self._trace_order.append(key)

    def trace_keys(self) -> list[str]:
        """Output variables in first-appearance order (valid after build())."""
        return list(self._trace_order)

    def _prune(self, node: _TraceNode, key: str) -> _TraceNode | None:
        """Copy of node keeping only branches that mention ``key``."""
        if node.kind == "assign":
            return node if node.key == key else None
        kept = [self._prune(c, key) for c in node.children]
        kept_nodes = [c for c in kept if c is not None]
        if node.kind != "root" and not kept_nodes:
            return None
        clone = _TraceNode(kind=node.kind, directive=node.directive)
        clone.children = kept_nodes
        return clone

    @staticmethod
    def _trace_is_computed(text: str) -> bool:
        return _COMPUTED.match(text) is not None

    def _render_assign_lines(self, node: _TraceNode, indent: int) -> list[str]:
        pad = "  " * indent
        suffix = " [ignored]" if node.ignored else ""
        out = [f"{pad}{node.text}{suffix}"]
        if (
            not node.ignored
            and self._trace_is_computed(node.text or "")
            and node.value is not None
        ):
            out.append(f"{pad}{node.key}={node.value}")
        return out

    def _render_directive_children(
        self, children: list[_TraceNode], indent: int
    ) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(children):
            c = children[i]
            if c.kind == "directive":
                out += self._render_node(c, indent)
                i += 1
                continue
            source = c.source
            out.append(f"{'  ' * indent}- {source}")
            while (
                i < len(children)
                and children[i].kind == "assign"
                and children[i].source == source
            ):
                out += self._render_assign_lines(children[i], indent + 1)
                i += 1
        return out

    def _render_node(self, node: _TraceNode, indent: int) -> list[str]:
        if node.kind == "directive":
            out = [f"{'  ' * indent}- {node.directive}"]
            out += self._render_directive_children(node.children, indent + 1)
            return out
        # top-level direct assign
        tag = (
            " [local]"
            if self._local_label is not None and node.source == self._local_label
            else ""
        )
        pad = "  " * indent
        out = [f"{pad}- {node.text}{tag}"]
        if self._trace_is_computed(node.text or "") and node.value is not None:
            out.append(f"{'  ' * (indent + 1)}{node.key}={node.value}")
        return out

    def _render_variable(self, key: str) -> list[str]:
        assert self._trace is not None
        out = [f"{key}={self._trace_final[key]}"]
        pruned = self._prune(self._trace.root, key)
        assert pruned is not None
        for child in pruned.children:
            out += self._render_node(child, 1)
        return out

    def trace_text(self, keys: list[str]) -> str:
        """Render a locked-format provenance report for the given keys."""
        assert self._trace is not None
        lines: list[str] = []
        for key in keys:
            lines += self._render_variable(key)
        return "\n".join(lines) + "\n" if lines else ""

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

    def _stem_bases(self, name: str) -> list[Path]:
        bang = name.startswith("!")
        core = name[1:] if bang else name
        validate_component(core, kind="fragment")
        base = data_dir()
        if bang:
            return [base / core]
        stems = [base / core]
        stems += [base / profile / core for profile in self.profiles]
        stems.append(base / self.target / core)
        stems += [base / self.target / profile / core for profile in self.profiles]
        return stems

    def _search_records(self, name: str) -> list[_Stem]:
        base = data_dir()
        root = base.resolve()
        records: list[_Stem] = []
        for stem in self._stem_bases(name):
            age = stem.with_name(stem.name + AGE_SUFFIX)
            env = stem.with_name(stem.name + ENV_SUFFIX)
            age_exists, env_exists = age.is_file(), env.is_file()
            # Symlinks are allowed, but any existing variant must stay inside emergenv/.
            for variant, exists in ((age, age_exists), (env, env_exists)):
                if exists and root not in variant.resolve().parents:
                    raise EmergenvError(
                        f"{name!r} resolves outside '{DATA_DIR_NAME}/' via {variant}"
                    )
            records.append(
                _Stem(
                    stem.relative_to(base).as_posix(), age, env, age_exists, env_exists
                )
            )
        return records


def build_target(
    target: str,
    profiles: list[str],
    *,
    mark_source: bool = True,
    bare: bool = False,
    local: bool = True,
    verbose: bool = False,
) -> str:
    """Build ``<target>`` into the final ``.env`` text (see module docstring).

    For a build whose variable trace you also want, use :func:`build_with_trace`.
    """
    return _Builder(
        target,
        profiles,
        mark_source=mark_source,
        bare=bare,
        local=local,
        verbose=verbose,
    ).build()


def build_with_trace(
    target: str,
    profiles: list[str],
    *,
    mark_source: bool = True,
    bare: bool = False,
    local: bool = True,
    verbose: bool = False,
) -> tuple[str, _Builder]:
    """Build with tracing enabled; return (text, builder) so the caller can
    render the variable trace via ``builder.trace_text(...)``."""
    builder = _Builder(
        target,
        profiles,
        mark_source=mark_source,
        bare=bare,
        local=local,
        verbose=verbose,
        trace=True,
    )
    return builder.build(), builder
