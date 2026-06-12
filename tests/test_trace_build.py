"""--trace provenance tree: structure recording (values come in Task 4)."""

from __future__ import annotations

from types import SimpleNamespace

from conftest import Run, requires_age

from emergenv.merge import _Builder


@requires_age
def test_include_records_each_source(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include base\n")
    workdir.write_env("base.env", "A=1\nB=2\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    top = b._trace.root.children
    assert top[0].kind == "directive"
    assert top[0].directive == "@include base"
    assert [(c.key, c.ignored) for c in top[0].children] == [("A", False), ("B", False)]
    assert [c.source for c in top[0].children] == [
        "emergenv/base.env",
        "emergenv/base.env",
    ]


@requires_age
def test_direct_assign_recorded_at_top_level(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    assert b._trace.root.children[0].kind == "assign"
    assert b._trace.root.children[0].key == "A"
    assert b._trace.root.children[0].text == "A=1"


@requires_age
def test_keyref_records_ignored_then_winner(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "MY=1\nMY=2\n")  # MY=1 ignored, MY=2 wins
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    assert d.kind == "directive" and d.directive == "@MY=base"
    assert [(c.text, c.ignored) for c in d.children] == [
        ("MY=1", True),
        ("MY=2", False),
    ]


@requires_age
def test_keyref_winner_only_no_ignored(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "MY=9\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    assert [(c.text, c.ignored) for c in d.children] == [("MY=9", False)]


@requires_age
def test_no_trace_when_disabled(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    b = _Builder("dot", [])  # trace defaults off
    b.build()
    assert b._trace is None


# --- additional coverage -------------------------------------------------------


@requires_age
def test_multiple_top_level_directives_recorded(workdir: SimpleNamespace) -> None:
    """Multiple directives at the top level each get their own directive node."""
    (workdir.root / "dot.emerg.env").write_text("@include base\n@include extra\n")
    workdir.write_env("base.env", "A=1\n")
    workdir.write_env("extra.env", "B=2\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    top = b._trace.root.children
    assert len(top) == 2
    assert top[0].directive == "@include base"
    assert top[1].directive == "@include extra"
    assert top[0].children[0].key == "A"
    assert top[1].children[0].key == "B"


@requires_age
def test_direct_assign_and_include_mixed(workdir: SimpleNamespace) -> None:
    """Direct assigns and includes coexist at the top level."""
    (workdir.root / "dot.emerg.env").write_text("X=0\n@include base\nY=99\n")
    workdir.write_env("base.env", "A=1\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    top = b._trace.root.children
    assert top[0].kind == "assign" and top[0].key == "X"
    assert top[1].kind == "directive" and top[1].directive == "@include base"
    assert top[2].kind == "assign" and top[2].key == "Y"


@requires_age
def test_export_keyref_recorded(workdir: SimpleNamespace) -> None:
    """export @KEY=frag is recorded with the export prefix in the directive label."""
    (workdir.root / "dot.emerg.env").write_text("export @MY=base\n")
    workdir.write_env("base.env", "MY=7\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    assert d.kind == "directive"
    assert d.directive == "export @MY=base"
    assert d.children[0].text == "export MY=7"
    assert d.children[0].ignored is False


@requires_age
def test_nested_include_records_inner_assigns(workdir: SimpleNamespace) -> None:
    """An @include inside an included fragment is recorded as a nested directive."""
    (workdir.root / "dot.emerg.env").write_text("@include outer\n")
    workdir.write_env("outer.env", "@include inner\n")
    workdir.write_env("inner.env", "Z=42\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    top = b._trace.root.children
    outer_node = top[0]
    assert outer_node.directive == "@include outer"
    # The inner include creates a nested directive node inside outer
    inner_node = outer_node.children[0]
    assert inner_node.kind == "directive"
    assert inner_node.directive == "@include inner"
    assert inner_node.children[0].key == "Z"


@requires_age
def test_keyref_winner_source_is_inner_file(workdir: SimpleNamespace) -> None:
    """The winning assign's source points to the fragment file, not the base."""
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "MY=hello\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    winner = d.children[0]
    assert winner.source == "emergenv/base.env"


@requires_age
def test_keyref_multiple_ignored_recorded_in_order(workdir: SimpleNamespace) -> None:
    """When there are multiple ignored candidates they are all recorded in order."""
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "MY=1\nMY=2\nMY=3\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    pairs = [(c.text, c.ignored) for c in d.children]
    assert pairs == [("MY=1", True), ("MY=2", True), ("MY=3", False)]


@requires_age
def test_blank_lines_not_recorded_as_assigns(workdir: SimpleNamespace) -> None:
    """Blank lines inside the base file do not produce assign nodes."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n\nB=2\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    keys = [c.key for c in b._trace.root.children if c.kind == "assign"]
    assert keys == ["A", "B"]


@requires_age
def test_comment_lines_not_recorded_as_assigns(workdir: SimpleNamespace) -> None:
    """Comment lines inside the base file do not produce assign nodes."""
    (workdir.root / "dot.emerg.env").write_text("# a comment\nA=1\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    keys = [c.key for c in b._trace.root.children if c.kind == "assign"]
    assert keys == ["A"]


@requires_age
def test_keyref_computed_winner_records_template_text(
    workdir: SimpleNamespace,
) -> None:
    """A keyref whose winner is computed records the $marker/template form."""
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "$MY=$(( 2 * 3 ))\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    assert d.directive == "@MY=base"
    assert [(c.text, c.ignored) for c in d.children] == [("$MY=$(( 2 * 3 ))", False)]


@requires_age
def test_keyref_computed_ignored_candidate_keeps_template_text(
    workdir: SimpleNamespace,
) -> None:
    """An ignored computed candidate keeps its original $marker/template text."""
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "$MY=$(( 1 ))\nMY=9\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    assert [(c.text, c.ignored) for c in d.children] == [
        ("$MY=$(( 1 ))", True),
        ("MY=9", False),
    ]


# ---------------------------------------------------------------------------
# Task 4: fill resolved values onto the trace tree
# ---------------------------------------------------------------------------


@requires_age
def test_values_filled_including_computed(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=2\n$B=$(( A * 3 ))\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    a, bnode = b._trace.root.children
    assert a.value == "2"
    assert bnode.value == "6"  # 2 * 3, evaluated in document order


@requires_age
def test_ignored_nodes_have_no_value(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "MY=1\nMY=2\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    assert d.children[0].ignored and d.children[0].value is None  # MY=1 ignored
    assert d.children[1].value == "2"  # winner


@requires_age
def test_final_values_and_order(workdir: SimpleNamespace) -> None:
    # B assigned before A; A overridden later -> final A is the last A
    (workdir.root / "dot.emerg.env").write_text("B=2\nA=1\nA=9\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b.trace_keys() == ["B", "A"]  # first-appearance order
    assert b._trace_final == {"B": "2", "A": "9"}  # last-wins per key


@requires_age
def test_keyref_computed_winner_value(workdir: SimpleNamespace) -> None:
    # keyref pulls a computed template; it evaluates where the keyref sits
    (workdir.root / "dot.emerg.env").write_text("SEED=5\n@D=base\n")
    workdir.write_env("base.env", "$D=$(( SEED * 2 ))\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[1]  # the @D=base directive
    assert d.children[-1].value == "10"  # 5 * 2 in the outer namespace


@requires_age
def test_plain_only_build_values_are_literals(workdir: SimpleNamespace) -> None:
    """Plain assignments: values are the literal RHS, no computation."""
    (workdir.root / "dot.emerg.env").write_text("X=hello\nY=world\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    x, y = b._trace.root.children
    assert x.value == "hello"
    assert y.value == "world"


@requires_age
def test_computed_point_in_time_value_preserved(workdir: SimpleNamespace) -> None:
    """A computed line sees the value of A that was defined above it, even if A
    is later overridden further down in the file."""
    (workdir.root / "dot.emerg.env").write_text("A=3\n$B=$(( A * 2 ))\nA=99\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    a1, bnode, a2 = b._trace.root.children
    assert a1.value == "3"
    assert bnode.value == "6"  # A was 3 at the time of B's evaluation
    assert a2.value == "99"


@requires_age
def test_iter_assign_nodes_skips_ignored(workdir: SimpleNamespace) -> None:
    """_iter_assign_nodes must skip ignored candidates."""
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "MY=1\nMY=2\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    nodes = list(b._iter_assign_nodes(b._trace.root))
    assert len(nodes) == 1
    assert nodes[0].ignored is False
    assert nodes[0].key == "MY"


@requires_age
def test_trace_keys_empty_before_build(workdir: SimpleNamespace) -> None:
    """trace_keys() is safe to call before build() — returns empty list."""
    b = _Builder("dot", [], trace=True)
    assert b.trace_keys() == []


@requires_age
def test_trace_keys_empty_when_trace_disabled(workdir: SimpleNamespace) -> None:
    """trace_keys() returns [] when tracing is disabled."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    b = _Builder("dot", [])
    b.build()
    assert b.trace_keys() == []


# ---------------------------------------------------------------------------
# Task 5: trace_text renderer
# ---------------------------------------------------------------------------


@requires_age
def test_trace_text_matches_oracle(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text(
        "@include base\n$MY_VAR=$(( MY_VAR + 1 ))\n@MY_VAR=base\n"
        "@MY_VAR=!profile/base\nMY_VAR=1\n"
    )
    workdir.write_env("base.env", "MY_VAR=1\n")
    workdir.write_env("profile/base.env", "MY_VAR=2\n")
    workdir.write_env("dot/profile/base.env", "$MY_VAR=$(( MY_VAR * 2 ))\n")
    b = _Builder("dot", ["profile"], trace=True)
    b.build()
    expected = """MY_VAR=1
  - @include base
    - emergenv/base.env
      MY_VAR=1
    - emergenv/profile/base.env
      MY_VAR=2
    - emergenv/dot/profile/base.env
      $MY_VAR=$(( MY_VAR * 2 ))
      MY_VAR=4
  - $MY_VAR=$(( MY_VAR + 1 ))
    MY_VAR=5
  - @MY_VAR=base
    - emergenv/base.env
      MY_VAR=1 [ignored]
    - emergenv/profile/base.env
      MY_VAR=2 [ignored]
    - emergenv/dot/profile/base.env
      $MY_VAR=$(( MY_VAR * 2 ))
      MY_VAR=10
  - @MY_VAR=!profile/base
    - emergenv/profile/base.env
      MY_VAR=2
  - MY_VAR=1
"""
    assert b.trace_text(["MY_VAR"]) == expected


@requires_age
def test_trace_text_plain_only(workdir: SimpleNamespace) -> None:
    """A plain-only variable: header + one  - line, no value sub-line."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    expected = "A=1\n  - A=1\n"
    assert b.trace_text(["A"]) == expected


@requires_age
def test_trace_text_local_tag(workdir: SimpleNamespace) -> None:
    """A direct assign from the local file is tagged [local]."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    (workdir.root / "dot.local.emerg.env").write_text("A=2\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    # Find the source label used for the local file assign
    local_node = [c for c in b._trace.root.children if c.source and "local" in c.source]
    assert local_node, "expected a local assign node"
    local_source = local_node[0].source
    # Manually wire _local_label (Task 6 wires it automatically)
    b._local_label = local_source
    text = b.trace_text(["A"])
    assert " [local]" in text
    # The plain (non-local) line has no tag
    lines = text.splitlines()
    local_lines = [ln for ln in lines if "[local]" in ln]
    assert len(local_lines) == 1
    assert local_lines[0].startswith("  - A=2 [local]")


@requires_age
def test_trace_text_keyref_plain_winner(workdir: SimpleNamespace) -> None:
    """A keyref with a plain winner: no value sub-line under the winner."""
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "MY=hello\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    text = b.trace_text(["MY"])
    expected = (
        "MY=hello\n" "  - @MY=base\n" "    - emergenv/base.env\n" "      MY=hello\n"
    )
    assert text == expected


@requires_age
def test_trace_text_two_variables(workdir: SimpleNamespace) -> None:
    """trace_text with two keys concatenates them in given order."""
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    text = b.trace_text(["A", "B"])
    expected = "A=1\n  - A=1\nB=2\n  - B=2\n"
    assert text == expected
    # Reversed order
    text_rev = b.trace_text(["B", "A"])
    assert text_rev == "B=2\n  - B=2\nA=1\n  - A=1\n"


@requires_age
def test_trace_text_computed_direct_shows_value_line(workdir: SimpleNamespace) -> None:
    """A computed direct assign shows the KEY=value second line."""
    (workdir.root / "dot.emerg.env").write_text("A=3\n$B=$(( A * 2 ))\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    text = b.trace_text(["B"])
    expected = "B=6\n" "  - $B=$(( A * 2 ))\n" "    B=6\n"
    assert text == expected


@requires_age
def test_trace_text_empty_keys_is_empty(workdir: SimpleNamespace) -> None:
    """trace_text([]) renders nothing."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b.trace_text([]) == ""


@requires_age
def test_trace_text_ignored_computed_has_no_value_line(
    workdir: SimpleNamespace,
) -> None:
    """An ignored computed keyref candidate renders its template, no value line."""
    (workdir.root / "dot.emerg.env").write_text("@MY=base\n")
    workdir.write_env("base.env", "$MY=$(( 1 + 1 ))\nMY=9\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    expected = (
        "MY=9\n"
        "  - @MY=base\n"
        "    - emergenv/base.env\n"
        "      $MY=$(( 1 + 1 )) [ignored]\n"
        "      MY=9\n"
    )
    assert b.trace_text(["MY"]) == expected


@requires_age
def test_trace_text_nested_include_renders(workdir: SimpleNamespace) -> None:
    """A nested @include renders as a nested directive block."""
    (workdir.root / "dot.emerg.env").write_text("@include outer\n")
    workdir.write_env("outer.env", "@include inner\n")
    workdir.write_env("inner.env", "Z=42\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    expected = (
        "Z=42\n"
        "  - @include outer\n"
        "    - @include inner\n"
        "      - emergenv/inner.env\n"
        "        Z=42\n"
    )
    assert b.trace_text(["Z"]) == expected


# ---------------------------------------------------------------------------
# Task 6: CLI --trace / --trace-all integration
# ---------------------------------------------------------------------------


@requires_age
def test_cli_trace_single_var(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n")
    r = run("build", "dot", "--trace", "A")
    assert r.code == 0
    assert "A=1\n  - A=1" in r.out
    assert "  - B=2" not in r.out  # B not traced


@requires_age
def test_cli_trace_star_all(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n")
    r = run("build", "dot", "--trace", "*")
    assert "  - A=1" in r.out and "  - B=2" in r.out


@requires_age
def test_cli_trace_all_flag(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n")
    r = run("build", "dot", "--trace-all")
    assert r.code == 0 and "  - A=1" in r.out and "  - B=2" in r.out


@requires_age
def test_cli_trace_all_respects_output_order(
    workdir: SimpleNamespace, run: Run
) -> None:
    (workdir.root / "dot.emerg.env").write_text("B=2\nA=1\n")
    r = run("build", "dot", "--trace-all")
    assert r.out.index("B=2") < r.out.index("A=1")


@requires_age
def test_cli_trace_repeatable_and_comma(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\nC=3\n")
    r = run("build", "dot", "--trace", "A,B", "--trace", "C")
    assert "  - A=1" in r.out and "  - B=2" in r.out and "  - C=3" in r.out


@requires_age
def test_cli_trace_unknown_var_errors(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    r = run("build", "dot", "--trace", "NOPE")
    assert r.code != 0
    assert "not in build output" in r.err


@requires_age
def test_cli_trace_suppressed_to_stdout(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    r = run("build", "dot", "--trace", "*", "--output", "-", "--bare")
    assert "  - A=1" not in r.out  # trace dropped; only built env on stdout
    assert r.out == "A=1\n"  # exactly the built env


@requires_age
def test_cli_trace_local_tag(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    (workdir.root / "dot.local.emerg.env").write_text("A=2\n")
    r = run("build", "dot", "--trace", "A")
    assert "[local]" in r.out  # the .local override line is tagged


@requires_age
def test_cli_trace_unknown_var_errors_to_stdout(
    workdir: SimpleNamespace, run: Run
) -> None:
    """An unknown --trace var errors even with --output -, and stdout stays clean."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    r = run("build", "dot", "--trace", "NOPE", "--output", "-")
    assert r.code != 0
    assert "not in build output" in r.err
    assert r.out == ""  # nothing written to stdout on a usage error


@requires_age
def test_cli_trace_all_suppressed_to_stdout(workdir: SimpleNamespace, run: Run) -> None:
    """--trace-all with --output - silently drops the trace (no error, clean env)."""
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    r = run("build", "dot", "--trace-all", "--output", "-", "--bare")
    assert r.code == 0
    assert r.out == "A=1\n"  # only the built env, no trace
