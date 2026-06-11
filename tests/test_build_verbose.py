"""--verbose flag is accepted and threaded through to the builder."""

from __future__ import annotations

from types import SimpleNamespace

from conftest import Run, requires_age


@requires_age
def test_verbose_flag_accepted(workdir: SimpleNamespace, run: Run) -> None:
    """The --verbose flag is accepted; the build succeeds and logs an import."""
    (workdir.root / "dot.emerg.env").write_text("@include db\n")
    workdir.write_env("db.env", "X=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "importing: " in result.out


@requires_age
def test_verbose_short_flag_accepted(workdir: SimpleNamespace, run: Run) -> None:
    """The -v short flag is also accepted."""
    (workdir.root / "dot.emerg.env").write_text("@include db\n")
    workdir.write_env("db.env", "X=1\n")
    result = run("build", "dot", "-v")
    assert result.code == 0
    assert "importing: " in result.out


# ---------------------------------------------------------------------------
# Task 4: per-fragment verbose breakdown
# ---------------------------------------------------------------------------


@requires_age
def test_verbose_age_only_is_using(workdir: SimpleNamespace, run: Run) -> None:
    """age present, env absent -> 'using: frag.age' and 'missing: frag.env'."""
    (workdir.root / "dot.emerg.env").write_text("@include frag\n")
    workdir.write_age("frag.age", "X=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "  - using: frag.age" in result.out
    assert "  - missing: frag.env" in result.out


@requires_age
def test_verbose_both_match_ignores_env(workdir: SimpleNamespace, run: Run) -> None:
    """age and env present with identical plaintext -> match tag."""
    (workdir.root / "dot.emerg.env").write_text("@include frag\n")
    workdir.write_age("frag.age", "X=1\n")
    workdir.write_env("frag.env", "X=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "  - using: frag.age" in result.out
    assert "  - ignoring: frag.env [age-preferred,match]" in result.out


@requires_age
def test_verbose_both_mismatch(workdir: SimpleNamespace, run: Run) -> None:
    """age and env present with different plaintext -> mismatch tag."""
    (workdir.root / "dot.emerg.env").write_text("@include frag\n")
    workdir.write_age("frag.age", "X=from_age\n")
    workdir.write_env("frag.env", "X=from_env\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "  - using: frag.age" in result.out
    assert "  - ignoring: frag.env [age-preferred,mismatch]" in result.out


@requires_age
def test_verbose_env_only(workdir: SimpleNamespace, run: Run) -> None:
    """env present, age absent -> 'missing: frag.age [env-present]' and 'using: frag.env [age-missing]'."""
    (workdir.root / "dot.emerg.env").write_text("@include frag\n")
    workdir.write_env("frag.env", "X=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "  - missing: frag.age [env-present]" in result.out
    assert "  - using: frag.env [age-missing]" in result.out


@requires_age
def test_verbose_both_present_age_undecryptable(
    workdir: SimpleNamespace, run: Run
) -> None:
    """age and env present but the age won't decrypt -> both lines red [undecryptable]."""
    (workdir.root / "dot.emerg.env").write_text("@include frag\n")
    (workdir.data / "frag.age").write_bytes(b"not a real age file")
    workdir.write_env("frag.env", "X=1\n")
    result = run("build", "dot", "--verbose")
    # The breakdown classifies the pair before the build fails reading the age.
    assert "  - using: frag.age [undecryptable]" in result.out
    assert "  - ignoring: frag.env [age-preferred,undecryptable]" in result.out
    assert result.code != 0  # reading the corrupt age then aborts the build


@requires_age
def test_verbose_both_absent_collapses(workdir: SimpleNamespace, run: Run) -> None:
    """A profile dir with no such fragment -> single collapsed missing line."""
    (workdir.root / "dot.emerg.env").write_text("@include frag\n")
    # Only present in the global location; use a profile whose dir has nothing.
    workdir.write_env("frag.env", "X=1\n")
    result = run("build", "dot", "--verbose", "--profile", "ghost")
    assert result.code == 0
    assert "  - missing: ghost/frag.(age|env)" in result.out


@requires_age
def test_verbose_header_is_fragment_name(workdir: SimpleNamespace, run: Run) -> None:
    """The breakdown header is 'importing: <name>' (the name as written)."""
    (workdir.root / "dot.emerg.env").write_text("@include frag\n")
    workdir.write_env("frag.env", "X=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "importing: frag" in result.out


@requires_age
def test_verbose_header_includes_bang(workdir: SimpleNamespace, run: Run) -> None:
    """The breakdown header preserves leading '!' in the fragment name."""
    (workdir.root / "dot.emerg.env").write_text("@include !frag\n")
    workdir.write_env("frag.env", "X=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "importing: !frag" in result.out


@requires_age
def test_verbose_nested_includes_not_interleaved(
    workdir: SimpleNamespace, run: Run
) -> None:
    """Outer fragment breakdown is fully printed before nested 'importing: base' header."""
    (workdir.root / "dot.emerg.env").write_text("@include service\n")
    workdir.write_env("service.env", "@include base\nA=1\n")
    workdir.write_env("base.env", "B=2\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    out = result.out
    assert out.index("importing: base") > out.index("importing: service")


@requires_age
def test_verbose_no_match_still_prints_breakdown(
    workdir: SimpleNamespace, run: Run
) -> None:
    """Even when no file matches, the breakdown is printed before the error."""
    (workdir.root / "dot.emerg.env").write_text("@include missing\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 1
    assert "importing: missing" in result.out
    assert "  - missing: missing.(age|env)" in result.out


@requires_age
def test_verbose_non_verbose_unaffected(workdir: SimpleNamespace, run: Run) -> None:
    """Without --verbose, terse 'importing: <path>' lines are logged as before."""
    (workdir.root / "dot.emerg.env").write_text("@include frag\n")
    workdir.write_env("frag.env", "X=1\n")
    result = run("build", "dot")
    assert result.code == 0
    # terse style: path-based log (relative to emergenv/)
    assert "importing: frag.env" in result.out
    # verbose breakdown lines must NOT appear
    assert "  - using:" not in result.out
    assert "  - missing:" not in result.out


# ---------------------------------------------------------------------------
# Task 5: base file + .local verbose breakdown
# ---------------------------------------------------------------------------


@requires_age
def test_base_emergenv_age(workdir: SimpleNamespace, run: Run) -> None:
    """cwd base absent; emergenv/dot.emerg.age present -> age/env table shown."""
    workdir.write_age("dot.emerg.age", "A=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "target: dot" in result.out
    assert "  - missing: dot.emerg.env [cwd]" in result.out
    assert "  - using: emergenv/dot.emerg.age" in result.out


@requires_age
def test_base_accident_all_red_ambiguous(workdir: SimpleNamespace, run: Run) -> None:
    """cwd base AND emergenv/dot.emerg.age both present -> ambiguous accident."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    workdir.write_age("dot.emerg.age", "A=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "  - using: dot.emerg.env [ambiguous]" in result.out
    assert "  - ignoring: emergenv/dot.emerg.age [ambiguous]" in result.out
    # the non-existent emergenv/ env slot is a plain green missing line, not red
    assert "  - missing: emergenv/dot.emerg.env" in result.out


@requires_age
def test_base_cwd_only(workdir: SimpleNamespace, run: Run) -> None:
    """Only cwd base present -> [cwd] label and collapsed emergenv/ missing line."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "  - using: dot.emerg.env [cwd]" in result.out
    assert "  - missing: emergenv/dot.emerg.(age|env)" in result.out


@requires_age
def test_local_missing(workdir: SimpleNamespace, run: Run) -> None:
    """cwd base present, no .local -> 'local: missing ...' in verbose output."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "local: missing " in result.out


@requires_age
def test_local_skipped(workdir: SimpleNamespace, run: Run) -> None:
    """.local present but --no-local passed -> 'local: skipped ... [--no-local]'."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    (workdir.root / "dot.local.emerg.env").write_text("B=2\n")
    result = run("build", "dot", "--verbose", "--no-local")
    assert result.code == 0
    assert "local: skipped " in result.out
    assert "[--no-local]" in result.out


@requires_age
def test_local_using(workdir: SimpleNamespace, run: Run) -> None:
    """.local present, no --no-local -> 'local: using ...' in verbose output."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    (workdir.root / "dot.local.emerg.env").write_text("B=2\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0
    assert "local: using " in result.out


@requires_age
def test_verbose_does_not_change_exit_code(workdir: SimpleNamespace, run: Run) -> None:
    """A tree with a red row (env-present-without-age) still exits 0."""
    workdir.write_env("dot.emerg.env", "A=1\n")
    # env-only fragment -> red row, but build still succeeds
    workdir.write_env("frag.env", "B=2\n")
    (workdir.root / "dot.emerg.env").write_text("@include frag\nA=1\n")
    result = run("build", "dot", "--verbose")
    assert result.code == 0


@requires_age
def test_verbose_suppressed_to_stdout(workdir: SimpleNamespace, run: Run) -> None:
    """--verbose with --output - suppresses all log lines; only built env on stdout."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    result = run("build", "dot", "--verbose", "--output", "-")
    assert "importing:" not in result.out
    assert "target:" not in result.out
    assert "  - " not in result.out
