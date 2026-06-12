"""@include key filter: whitelist (K), blacklist (!K), and the error cases."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

from emergenv import EmergenvError
from emergenv.merge import _Builder


@requires_age
def test_whitelist_keeps_only_named_keys(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db A C\n")
    workdir.write_env("db.env", "A=1\nB=2\nC=3\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\nC=3\n"


@requires_age
def test_blacklist_drops_named_keeps_rest(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db !B\n")
    workdir.write_env("db.env", "A=1\nB=2\nC=3\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\nC=3\n"


@requires_age
def test_whitelist_all_lines_travel(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include f MY\n")
    workdir.write_env("f.env", "MY=1\n$MY=$(( MY + 1 ))\n")
    # both MY lines travel into the build, so the expression reads the prior line
    assert _Builder("dot", [], bare=True).build() == "MY=2\n"


@requires_age
def test_mixing_is_error(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db A !B\n")
    workdir.write_env("db.env", "A=1\nB=2\n")
    with pytest.raises(EmergenvError, match="mix"):
        _Builder("dot", []).build()


@requires_age
def test_unknown_positive_key_errors(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db NOPE\n")
    workdir.write_env("db.env", "A=1\n")
    with pytest.raises(EmergenvError, match="NOPE"):
        _Builder("dot", []).build()


@requires_age
def test_unknown_excluded_key_errors(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db !NOPE\n")
    workdir.write_env("db.env", "A=1\n")
    with pytest.raises(EmergenvError, match="NOPE"):
        _Builder("dot", []).build()


@requires_age
def test_invalid_key_token_errors(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db 1BAD\n")
    workdir.write_env("db.env", "A=1\n")
    with pytest.raises(EmergenvError):
        _Builder("dot", []).build()


@requires_age
def test_blacklist_keeps_comments_whitelist_drops_them(
    workdir: SimpleNamespace,
) -> None:
    workdir.write_env("db.env", "# note\nA=1\nB=2\n")
    (workdir.root / "dot.emerg.env").write_text("@include db !B\n")
    out = _Builder("dot", []).build()  # not bare
    assert "# note" in out and "B=2" not in out
    (workdir.root / "dot.emerg.env").write_text("@include db A\n")
    assert "# note" not in _Builder("dot", []).build()


@requires_age
def test_whitelist_preserves_override_history(workdir: SimpleNamespace) -> None:
    workdir.write_env("db.env", "PORT=1\n")
    workdir.write_env("prod/db.env", "PORT=2\n")
    (workdir.root / "dot.emerg.env").write_text("@include db PORT\n")
    out = _Builder("dot", ["prod"]).build()  # not bare -> loser commented
    assert "# PORT=1" in out and "PORT=2" in out


@requires_age
def test_plain_include_unchanged(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db\n")
    workdir.write_env("db.env", "A=1\nB=2\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\nB=2\n"


@requires_age
def test_whitelist_multiple_positives(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db A B\n")
    workdir.write_env("db.env", "A=1\nB=2\nC=3\nD=4\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\nB=2\n"


@requires_age
def test_blacklist_multiple_exclusions(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db !B !C\n")
    workdir.write_env("db.env", "A=1\nB=2\nC=3\nD=4\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\nD=4\n"


@requires_age
def test_whitelist_with_two_profiles(workdir: SimpleNamespace) -> None:
    workdir.write_env("db.env", "HOST=base\nPORT=5432\n")
    workdir.write_env("prod/db.env", "HOST=prod\n")
    (workdir.root / "dot.emerg.env").write_text("@include db HOST\n")
    out = _Builder("dot", ["prod"]).build()  # not bare -> loser commented
    # base HOST loses, prod HOST wins
    assert "HOST=prod" in out and "# HOST=base" in out
    # PORT is filtered out entirely
    assert "PORT" not in out


@requires_age
def test_bang_fragment_with_whitelist(workdir: SimpleNamespace) -> None:
    """``@include !db A`` (absolute-name form) should also accept key filters."""
    workdir.write_env("db.env", "A=1\nB=2\n")
    (workdir.root / "dot.emerg.env").write_text("@include !db A\n")
    assert _Builder("dot", [], bare=True).build() == "A=1\n"


@requires_age
def test_invalid_negative_key_token_errors(workdir: SimpleNamespace) -> None:
    """``!1bad`` has an invalid bare name and should error."""
    (workdir.root / "dot.emerg.env").write_text("@include db !1bad\n")
    workdir.write_env("db.env", "A=1\n")
    with pytest.raises(EmergenvError):
        _Builder("dot", []).build()


@requires_age
def test_whitelist_single_key_only(workdir: SimpleNamespace) -> None:
    """A single-key whitelist keeps only that one key."""
    (workdir.root / "dot.emerg.env").write_text("@include db X\n")
    workdir.write_env("db.env", "X=10\nY=20\nZ=30\n")
    assert _Builder("dot", [], bare=True).build() == "X=10\n"


# ---------------------------------------------------------------------------
# Trace correctness for filtered includes (Task 2)
# ---------------------------------------------------------------------------


@requires_age
def test_trace_whitelist_records_only_kept(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db A\n")
    workdir.write_env("db.env", "A=1\nB=2\n")
    b = _Builder("dot", [], trace=True)
    b.build()  # must NOT raise the value-fill length guard
    assert b._trace is not None
    d = b._trace.root.children[0]
    assert d.kind == "directive"
    assert d.directive == "@include db A"
    assert [c.key for c in d.children] == ["A"]  # B not recorded


@requires_age
def test_trace_blacklist_records_kept(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db !B\n")
    workdir.write_env("db.env", "A=1\nB=2\nC=3\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    assert d.directive == "@include db !B"
    assert [c.key for c in d.children] == ["A", "C"]


@requires_age
def test_trace_filtered_include_value_fill_no_crash_with_computed(
    workdir: SimpleNamespace,
) -> None:
    # a computed kept line + a dropped key: value-fill must still pair up 1:1
    (workdir.root / "dot.emerg.env").write_text("@include f MY\n")
    workdir.write_env("f.env", "MY=1\nOTHER=9\n$MY=$(( MY + 1 ))\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    # only MY lines recorded (OTHER dropped); winner value filled
    assert [c.key for c in d.children] == ["MY", "MY"]
    assert d.children[-1].value == "2"


@requires_age
def test_trace_text_renders_filtered_include(workdir: SimpleNamespace) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db A\n")
    workdir.write_env("db.env", "A=1\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    expected = "A=1\n  - @include db A\n    - emergenv/db.env\n      A=1\n"
    assert b.trace_text(["A"]) == expected


@requires_age
def test_cli_trace_filtered_include(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("@include db A\n")
    workdir.write_env("db.env", "A=1\nB=2\n")
    r = run("build", "dot", "--trace", "A")
    assert r.code == 0
    assert "  - @include db A" in r.out
    assert "B=2" not in r.out


@requires_age
def test_trace_blacklist_text_renders_kept_only(workdir: SimpleNamespace) -> None:
    """trace_text for a blacklist filtered include renders only kept keys."""
    (workdir.root / "dot.emerg.env").write_text("@include db !B\n")
    workdir.write_env("db.env", "A=1\nB=2\nC=3\n")
    b = _Builder("dot", [], trace=True)
    b.build()
    text_a = b.trace_text(["A"])
    assert "  - @include db !B" in text_a
    assert "B=2" not in text_a
    text_c = b.trace_text(["C"])
    assert "  - @include db !B" in text_c
    assert "B=2" not in text_c


@requires_age
def test_trace_filtered_include_dropped_key_not_in_output(
    workdir: SimpleNamespace, run: Run
) -> None:
    """A key dropped by the filter is absent from the build output; --trace on it errors."""
    (workdir.root / "dot.emerg.env").write_text("@include db A\n")
    workdir.write_env("db.env", "A=1\nB=2\n")
    r = run("build", "dot", "--trace", "B")
    assert r.code != 0
    assert "not in build output" in r.err


@requires_age
def test_trace_filtered_override_shows_loser(workdir: SimpleNamespace) -> None:
    """A filtered include with a base + profile override: trace shows both layers."""
    workdir.write_env("db.env", "HOST=base\nPORT=5432\n")
    workdir.write_env("prod/db.env", "HOST=prod\n")
    (workdir.root / "dot.emerg.env").write_text("@include db HOST\n")
    b = _Builder("dot", ["prod"], trace=True)
    b.build()
    assert b._trace is not None
    d = b._trace.root.children[0]
    assert d.directive == "@include db HOST"
    keys = [c.key for c in d.children]
    assert keys == ["HOST", "HOST"]  # base and prod both recorded
    # PORT never appears in the trace
    assert all(c.key != "PORT" for c in d.children)
