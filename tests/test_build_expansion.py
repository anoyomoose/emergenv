"""Integration tests for $KEY= / %KEY= computed-value expansion in the build
engine (the merge.py driver over the emergenv.expand library)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

from emergenv import EmergenvError
from emergenv.merge import build_target


@pytest.fixture
def bd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A plaintext build workspace: chdir'd, with an empty emergenv/ dir."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "emergenv").mkdir()

    def write_root(name: str, text: str) -> None:
        (tmp_path / name).write_text(text)

    def write_data(rel: str, text: str) -> None:
        path = tmp_path / "emergenv" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    return SimpleNamespace(root=tmp_path, write_root=write_root, write_data=write_data)


def active(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


# --- basic computed values --------------------------------------------------


def test_computed_reference(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "HOST=db\n$URL=http://${HOST}\n")
    out = build_target("my", [])
    assert active(out) == ["HOST=db", "URL=http://db"]


def test_computed_renders_as_comment_then_result(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "HOST=db\n$URL=http://${HOST}\n")
    out = build_target("my", [])
    assert "# COMPUTED: $URL=http://${HOST}" in out
    # the comment line precedes the resolved assignment
    lines = out.splitlines()
    i = lines.index("# COMPUTED: $URL=http://${HOST}")
    assert lines[i + 1] == "URL=http://db"


def test_bare_drops_computed_comment(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "HOST=db\n$URL=http://${HOST}\n")
    out = build_target("my", [], bare=True)
    assert "COMPUTED" not in out
    assert out == "HOST=db\nURL=http://db\n"


def test_plain_value_is_never_expanded(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "KEY=${HOST}\nHOST=db\n")
    assert active(build_target("my", [])) == ["KEY=${HOST}", "HOST=db"]


def test_arithmetic_in_computed(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "BASE=5000\n$PORT=$(( BASE + 80 ))\n")
    assert active(build_target("my", [])) == ["BASE=5000", "PORT=5080"]


def test_export_computed(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "HOST=db\nexport $URL=http://${HOST}\n")
    out = build_target("my", [])
    assert "export URL=http://db" in active(out)
    assert "# COMPUTED: export $URL=http://${HOST}" in out


def test_escaping_in_computed(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "$PRICE=$$5\n")
    assert active(build_target("my", [])) == ["PRICE=$5"]


# --- sequential live namespace ----------------------------------------------


def test_define_before_use_forward_reference_errors(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "$URL=${HOST}\nHOST=db\n")
    with pytest.raises(EmergenvError, match="HOST"):
        build_target("my", [])


def test_error_names_source_file(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "$URL=${MISSING}\n")
    with pytest.raises(EmergenvError, match="my.emerg.env"):
        build_target("my", [])


def test_reassignment_uses_live_value(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "PORT=1\n$A=${PORT}\nPORT=2\n$B=${PORT}\n")
    out = active(build_target("my", []))
    assert "A=1" in out and "B=2" in out and "PORT=2" in out


def test_self_reference_uses_prior_value(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "X=base\n$X=${X}-v2\n")
    assert active(build_target("my", [])) == ["X=base-v2"]


# --- $ vs % and the environment ---------------------------------------------

_ENV = "EMERGENV_EXPANSION_TEST_VAR"


def test_percent_reads_environment(
    bd: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_ENV, "fromenv")
    bd.write_root("my.emerg.env", f"%X=${{{_ENV}}}\n")
    assert active(build_target("my", [])) == ["X=fromenv"]


def test_dollar_does_not_see_environment(
    bd: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_ENV, "fromenv")
    bd.write_root("my.emerg.env", f"$X=${{{_ENV}}}\n")
    with pytest.raises(EmergenvError, match=_ENV):
        build_target("my", [])


def test_percent_local_wins_over_environment(
    bd: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_ENV, "fromenv")
    bd.write_root("my.emerg.env", f"{_ENV}=local\n%X=${{{_ENV}}}\n")
    assert "X=local" in active(build_target("my", []))


# --- marker travel through keyref -------------------------------------------


def test_marker_travels_through_keyref(bd: SimpleNamespace) -> None:
    # db fragment defines a computed URL; the parent pulls just that key and the
    # computed-ness travels, evaluating in the parent's context (HOST from base).
    bd.write_data("db.env", "$URL=http://${HOST}\n")
    bd.write_root("my.emerg.env", "HOST=db\n@URL=db\n")
    assert active(build_target("my", [])) == ["HOST=db", "URL=http://db"]


def test_keyref_computed_missing_dependency_errors(bd: SimpleNamespace) -> None:
    # the pulled template references HOST, which is NOT defined in the parent.
    bd.write_data("db.env", "$URL=http://${HOST}\n")
    bd.write_root("my.emerg.env", "@URL=db\n")
    with pytest.raises(EmergenvError, match="HOST"):
        build_target("my", [])


# --- CLI level: ExpansionError surfaces as a clean non-zero exit ------------


@requires_age
def test_cli_build_expansion_error_exits_nonzero(
    workdir: SimpleNamespace, run: Run
) -> None:
    (workdir.root / "dot.emerg.env").write_text("$X=${MISSING}\n")
    result = run("build", "dot")
    assert result.code == 1
    assert "MISSING" in result.err
    assert not (workdir.root / ".env").exists()  # nothing written on failure


# --- extra tests per mandate ------------------------------------------------


def test_computed_references_key_from_include(bd: SimpleNamespace) -> None:
    """A computed line referencing a key brought in by @include (not a plain line)."""
    bd.write_data("base.env", "HOST=included-db\n")
    bd.write_root("my.emerg.env", "@include base\n$URL=http://${HOST}\n")
    assert active(build_target("my", [])) == [
        "HOST=included-db",
        "URL=http://included-db",
    ]


def test_percent_unset_env_and_not_local_errors(
    bd: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """%KEY= where the env var is UNSET and not defined locally → error."""
    monkeypatch.delenv(_ENV, raising=False)
    bd.write_root("my.emerg.env", f"%X=${{{_ENV}}}\n")
    with pytest.raises(EmergenvError, match=_ENV):
        build_target("my", [])


def test_chained_computed(bd: SimpleNamespace) -> None:
    """A computed value used by a LATER computed line ($A=...; $B=${A}...)."""
    bd.write_root("my.emerg.env", "BASE=hello\n$A=${BASE}-world\n$B=${A}!\n")
    assert active(build_target("my", [])) == [
        "BASE=hello",
        "A=hello-world",
        "B=hello-world!",
    ]


def test_no_source_build_with_computed_line(bd: SimpleNamespace) -> None:
    """--no-source build with a computed line: no FROM: but COMPUTED: still present."""
    bd.write_root("my.emerg.env", "HOST=db\n$URL=http://${HOST}\n")
    out = build_target("my", [], mark_source=False)
    assert "FROM" not in out
    assert "# COMPUTED: $URL=http://${HOST}" in out
    assert active(out) == ["HOST=db", "URL=http://db"]


def test_computed_result_overridden_by_plain_last_wins(bd: SimpleNamespace) -> None:
    """A computed result that is later overridden by a plain assignment (last-wins
    comments the computed result)."""
    bd.write_root("my.emerg.env", "HOST=db\n$URL=http://${HOST}\nURL=override\n")
    out = build_target("my", [])
    # The computed result URL=http://db is overridden by URL=override
    assert active(out) == ["HOST=db", "URL=override"]
    # The computed intermediate result is commented out by last-wins
    assert "# URL=http://db" in out


def test_plain_keyref_unchanged(bd: SimpleNamespace) -> None:
    """Confirm a plain keyref still produces identical output."""
    bd.write_data("secrets.env", "DB_PASSWORD=s3cr3t\nOTHER=x\n")
    bd.write_root("my.emerg.env", "@DB_PASSWORD=secrets\n")
    out = build_target("my", [])
    assert active(out) == ["DB_PASSWORD=s3cr3t"]
    assert "# FROM: emergenv/secrets.env" in out
