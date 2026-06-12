"""@filter: output-only key filtering, applied after evaluation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

from emergenv import EmergenvError
from emergenv.merge import _Builder


@requires_age
def test_whitelist_keeps_only_named(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\nC=3\n@filter A C\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\nC=3\n"


@requires_age
def test_blacklist_drops_named(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\nC=3\n@filter !B\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\nC=3\n"


@requires_age
def test_hidden_key_still_evaluated(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text(
        "SEED=5\n$URL=$(( SEED * 2 ))\n@filter URL\n"
    )
    assert _Builder("dot", [], bare=True).build() == "URL=10\n"


@requires_age
def test_hidden_key_commented_in_nonbare(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n@filter A\n")
    out = _Builder("dot", []).build()
    assert "A=1" in out and "# B=2" in out


@requires_age
def test_multiple_filters_compose_in_order(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text(
        "A=1\nB=2\nC=3\n@filter A B\n@filter !B\n"
    )
    assert _Builder("dot", [], bare=True).build() == "A=1\n"


@requires_age
def test_whitelist_unknown_key_errors(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n@filter NOPE\n")
    with pytest.raises(EmergenvError, match="NOPE"):
        _Builder("dot", []).build()


@requires_age
def test_blacklist_unknown_key_is_fine(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n@filter !NOPE\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\n"


@requires_age
def test_no_keys_errors(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n@filter\n")
    with pytest.raises(EmergenvError):
        _Builder("dot", []).build()


@requires_age
def test_mixing_errors(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n@filter A !B\n")
    with pytest.raises(EmergenvError, match="mix"):
        _Builder("dot", []).build()


@requires_age
def test_filter_in_fragment_errors(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include frag\n")
    workdir.write_env("frag.env", "A=1\n@filter A\n")
    with pytest.raises(EmergenvError, match="fragment|base"):
        _Builder("dot", []).build()


@requires_age
def test_filter_keyref_lookalike_is_keyref(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@filter=db\n")
    workdir.write_env("db.env", "filter=on\n")
    assert _Builder("dot", [], bare=True).build() == "filter=on\n"


@requires_age
def test_whitelist_then_whitelist(workdir: SimpleNamespace) -> None:
    """Two whitelist filters intersect: only keys in both survive."""
    (workdir.root / "dot.emerg.env").write_text(
        "A=1\nB=2\nC=3\n@filter A B\n@filter B C\n"
    )
    assert _Builder("dot", [], bare=True).build() == "B=2\n"


@requires_age
def test_hidden_key_from_marker_in_nonbare(workdir: SimpleNamespace) -> None:
    """A hidden key's # COMPUTED: comment and winning line are both commented."""
    (workdir.root / "dot.emerg.env").write_text(
        "$SEED=$(( 1 + 1 ))\nVAL=hello\n@filter VAL\n"
    )
    out = _Builder("dot", []).build()
    # SEED's computed directive line is already prefixed with "# COMPUTED:"
    # After @filter, the winning SEED= line should also be commented out
    assert "VAL=hello" in out
    assert "# SEED=2" in out


@requires_age
def test_hidden_overridden_key_fully_commented(workdir: SimpleNamespace) -> None:
    """An overridden key that is also filtered out: both the override comment
    and the winning line become comments."""
    (workdir.root / "dot.emerg.env").write_text("X=1\nX=2\nY=3\n@filter Y\n")
    out = _Builder("dot", []).build()
    assert "Y=3" in out
    assert "# X=1" in out
    assert "# X=2" in out


@requires_age
def test_no_filter_param_short_circuits(workdir: SimpleNamespace) -> None:
    """no_filter=True ignores the @filter effect (and its unknown-key check)."""
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n@filter A\n")
    assert _Builder("dot", [], bare=True, no_filter=True).build() == "A=1\nB=2\n"


@requires_age
def test_filter_in_local_file_applies(workdir: SimpleNamespace) -> None:
    """@filter is honoured in the .local file (also a base-level input)."""
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n")
    (workdir.root / "dot.local.emerg.env").write_text("@filter A\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\n"


# ---------------------------------------------------------------------------
# Task 2: --no-filter CLI flag + trace [filtered] marker
# ---------------------------------------------------------------------------


@requires_age
def test_no_filter_shows_everything(workdir: SimpleNamespace, run: Run) -> None:
    """--no-filter causes @filter directives to be ignored; all keys emitted."""
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n@filter A\n")
    r = run("build", "dot", "--bare", "--no-filter", "--output", "-")
    assert r.out == "A=1\nB=2\n"
    r2 = run("build", "dot", "--bare", "--output", "-")
    assert r2.out == "A=1\n"


@requires_age
def test_trace_marks_filtered_key(workdir: SimpleNamespace) -> None:
    """A key hidden by @filter has ' [filtered]' appended to its trace header."""
    (workdir.root / "dot.emerg.env").write_text(
        "SEED=5\n$URL=$(( SEED * 2 ))\n@filter URL\n"
    )
    # @filter URL is a whitelist: URL is kept, SEED is filtered out.
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b.trace_text(["SEED"]).startswith("SEED=5 [filtered]\n")
    assert b.trace_text(["URL"]).startswith("URL=10\n")


@requires_age
def test_cli_trace_all_includes_filtered(workdir: SimpleNamespace, run: Run) -> None:
    """--trace-all marks filtered-out keys with [filtered] in the trace header."""
    # @filter A keeps A, so B is filtered out.
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n@filter A\n")
    r = run("build", "dot", "--trace-all")
    assert r.code == 0
    assert "A=1" in r.out and "B=2 [filtered]" in r.out


@requires_age
def test_no_filter_clears_trace_marker(workdir: SimpleNamespace, run: Run) -> None:
    """With --no-filter, no [filtered] marker appears anywhere in trace output."""
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n@filter A\n")
    r = run("build", "dot", "--no-filter", "--trace-all")
    assert "[filtered]" not in r.out


@requires_age
def test_trace_filtered_out_is_populated(workdir: SimpleNamespace) -> None:
    """_filtered_out reflects exactly which keys the filter hid."""
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\nC=3\n@filter A C\n")
    # whitelist A C: B is filtered out
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._filtered_out == frozenset({"B"})


@requires_age
def test_no_filter_leaves_filtered_out_empty(workdir: SimpleNamespace) -> None:
    """When no_filter=True, _filtered_out stays empty."""
    (workdir.root / "dot.emerg.env").write_text("A=1\nB=2\n@filter A\n")
    b = _Builder("dot", [], no_filter=True)
    b.build()
    assert b._filtered_out == frozenset()
