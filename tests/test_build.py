"""Tests for the build engine (emergenv.merge) and the `build` command."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import Run, requires_age

from emergenv import EmergenvError, crypto
from emergenv.cli import _parse_profiles
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


def _active(text: str) -> list[str]:
    """The uncommented, non-blank lines of build output (no FROM headers)."""
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


# --- basics -----------------------------------------------------------------


def test_build_basic_with_source_header(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\nB=2\n")
    out = build_target("my", [])
    assert "# FROM: my.emerg.env" in out
    assert _active(out) == ["A=1", "B=2"]


@pytest.mark.usefixtures("bd")
def test_build_no_base_errors() -> None:
    with pytest.raises(EmergenvError, match="no base to build"):
        build_target("my", [])


def test_build_local_overrides_base(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n")
    bd.write_root("my.local.emerg.env", "A=2\n")
    out = build_target("my", [])
    assert "# A=1" in out
    assert _active(out) == ["A=2"]


def test_build_no_local_skips_local_override(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n")
    bd.write_root("my.local.emerg.env", "A=2\n")
    assert _active(build_target("my", [], local=False)) == ["A=1"]
    # sanity: the override still applies by default
    assert _active(build_target("my", [])) == ["A=2"]


def test_build_no_local_skips_local_includes(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n")
    bd.write_root("my.local.emerg.env", "@include extra\n")
    bd.write_data("extra.env", "B=2\n")
    assert _active(build_target("my", [], local=False)) == ["A=1"]


def test_build_no_local_does_not_log_local(
    bd: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    bd.write_root("my.emerg.env", "A=1\n")
    bd.write_root("my.local.emerg.env", "A=2\n")
    build_target("my", [], local=False)
    assert "local:" not in capsys.readouterr().out


def test_build_comments_and_blanks_preserved(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "# hi\n\nA=1\n")
    out = build_target("my", [], mark_source=False)
    assert out == "# hi\n\nA=1\n"


# --- blank-line neatness ----------------------------------------------------


def test_build_strips_blank_edges(bd: SimpleNamespace) -> None:
    # leading/trailing blank lines (whitespace-only included) are trimmed off,
    # and the output ends with a single newline (no trailing blank line).
    bd.write_root("my.emerg.env", "\n   \nA=1\n   \n\n")
    assert build_target("my", [], mark_source=False) == "A=1\n"


def test_build_collapses_internal_blank_runs(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n\n\n\nB=2\n")
    assert build_target("my", [], mark_source=False) == "A=1\n\nB=2\n"


def test_build_keeps_single_internal_blank(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n\nB=2\n")
    assert build_target("my", [], mark_source=False) == "A=1\n\nB=2\n"


def test_build_drops_from_header_for_blank_only_source(bd: SimpleNamespace) -> None:
    # an empty included fragment between two base lines contributes only a blank;
    # its FROM header is dropped and the surrounding blanks collapse to one.
    bd.write_root("my.emerg.env", "A=1\n@include empty\nB=2\n")
    bd.write_data("empty.env", "\n   \n\n")
    out = build_target("my", [])
    assert "empty.env" not in out  # no FROM header for the blank-only source
    assert out == "# FROM: my.emerg.env\nA=1\n\nB=2\n"


def test_build_one_blank_line_between_base_and_local(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n")
    bd.write_root("my.local.emerg.env", "B=2\n")
    assert build_target("my", [], mark_source=False) == "A=1\n\nB=2\n"


def test_build_included_fragment_blank_edges_trimmed(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include frag\n")
    bd.write_data("frag.env", "\n\nF=1\n\n")
    assert build_target("my", [], mark_source=False) == "F=1\n"


def test_build_no_trailing_blank_when_base_ends_with_include(
    bd: SimpleNamespace,
) -> None:
    bd.write_root("my.emerg.env", "A=1\n@include frag\n")
    bd.write_data("frag.env", "F=1\n")
    assert build_target("my", [], mark_source=False) == "A=1\nF=1\n"


def test_build_no_source_markers(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n")
    out = build_target("my", [], mark_source=False)
    assert "FROM" not in out
    assert out == "A=1\n"


# --- @include ---------------------------------------------------------------


def test_include_splices(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include db\n")
    bd.write_data("db.env", "X=1\nY=2\n")
    out = build_target("my", [])
    assert "# FROM: emergenv/db.env" in out
    assert _active(out) == ["X=1", "Y=2"]


def test_include_profile_order_and_last_wins(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include db\n")
    bd.write_data("db.env", "X=base\n")
    bd.write_data("prod/db.env", "X=prod\n")
    out = build_target("my", ["prod"])
    assert "# X=base" in out
    assert _active(out) == ["X=prod"]


def test_include_bang_skips_search(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include !db\n")
    bd.write_data("db.env", "X=global\n")
    bd.write_data("prod/db.env", "X=prof\n")
    out = build_target("my", ["prod"])
    assert _active(out) == ["X=global"]


def test_include_no_match_errors(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include nope\n")
    with pytest.raises(EmergenvError, match="matched no file"):
        build_target("my", [])


def test_include_nested_provenance(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include a\n")
    bd.write_data("a.env", "@include b\n")
    bd.write_data("b.env", "Z=1\n")
    out = build_target("my", [])
    assert "# FROM: emergenv/b.env" in out
    assert _active(out) == ["Z=1"]


def test_include_cycle_errors(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include a\n")
    bd.write_data("a.env", "@include b\n")
    bd.write_data("b.env", "@include a\n")
    with pytest.raises(EmergenvError, match="cycle"):
        build_target("my", [])


def test_include_diamond_is_fine(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include a\n@include b\n")
    bd.write_data("a.env", "@include common\n")
    bd.write_data("b.env", "@include common\n")
    bd.write_data("common.env", "K=1\n")
    out = build_target("my", [])
    assert _active(out) == ["K=1"]  # last-wins collapses the two copies


# --- @<key>= ----------------------------------------------------------------


def test_keyref_extracts_value_with_provenance(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@DB_PASSWORD=secrets\n")
    bd.write_data("secrets.env", "DB_PASSWORD=s3cr3t\nOTHER=x\n")
    out = build_target("my", [])
    assert "# FROM: emergenv/secrets.env" in out
    assert _active(out) == ["DB_PASSWORD=s3cr3t"]


def test_keyref_missing_key_errors(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@MISSING=secrets\n")
    bd.write_data("secrets.env", "DB=v\n")
    with pytest.raises(EmergenvError, match="not found"):
        build_target("my", [])


def test_keyref_is_case_sensitive(bd: SimpleNamespace) -> None:
    bd.write_data("secrets.env", "foo=lower\nFOO=upper\n")
    bd.write_root("my.emerg.env", "@FOO=secrets\n")
    assert _active(build_target("my", [])) == ["FOO=upper"]


def test_keyref_case_sensitive_lower(bd: SimpleNamespace) -> None:
    bd.write_data("secrets.env", "foo=lower\nFOO=upper\n")
    bd.write_root("my.emerg.env", "@foo=secrets\n")
    assert _active(build_target("my", [])) == ["foo=lower"]


def test_keyref_export_emits_export(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "export @DB=secrets\n")
    bd.write_data("secrets.env", "DB=v\n")
    assert _active(build_target("my", [])) == ["export DB=v"]


def test_keyref_no_export_even_if_source_exports(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@DB=secrets\n")
    bd.write_data("secrets.env", "export DB=v\n")
    assert _active(build_target("my", [])) == ["DB=v"]


# --- export passthrough + last-wins -----------------------------------------


def test_export_assignment_participates_in_last_wins(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "export A=1\nA=2\n")
    out = build_target("my", [])
    assert "# export A=1" in out
    assert _active(out) == ["A=2"]


# --- malformed directives ---------------------------------------------------


def test_export_include_errors(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "export @include db\n")
    bd.write_data("db.env", "X=1\n")
    with pytest.raises(EmergenvError, match="export"):
        build_target("my", [])


def test_malformed_directive_errors(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@frobnicate\n")
    with pytest.raises(EmergenvError, match="malformed"):
        build_target("my", [])


def test_include_missing_name_errors(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include\n")
    with pytest.raises(EmergenvError, match="missing a fragment"):
        build_target("my", [])


# --- dot target -------------------------------------------------------------


def test_dot_target_builds(bd: SimpleNamespace) -> None:
    bd.write_root("dot.emerg.env", "A=1\n")
    assert _active(build_target("dot", [])) == ["A=1"]


# --- symlink containment ----------------------------------------------------


def test_symlink_inside_emergenv_is_allowed(bd: SimpleNamespace) -> None:
    (bd.root / "emergenv" / "sub").mkdir()
    bd.write_data("sub/db.env", "X=1\n")
    (bd.root / "emergenv" / "lnk").symlink_to(bd.root / "emergenv" / "sub")
    bd.write_root("my.emerg.env", "@include lnk/db\n")
    assert _active(build_target("my", [])) == ["X=1"]


def test_symlink_outside_emergenv_is_rejected(bd: SimpleNamespace) -> None:
    outside = bd.root / "outside"
    outside.mkdir()
    (outside / "x.env").write_text("X=1\n")
    (bd.root / "emergenv" / "out").symlink_to(outside)
    bd.write_root("my.emerg.env", "@include out/x\n")
    with pytest.raises(EmergenvError, match="outside"):
        build_target("my", [])


# --- encrypted inputs (real age) --------------------------------------------


@requires_age
def test_build_decrypts_age_include(workdir: SimpleNamespace) -> None:
    (workdir.root / "my.emerg.env").write_text("@include db\n")
    age = workdir.data / "db.age"
    age.write_bytes(crypto.encrypt_verified(b"X=secret\n", age))
    assert _active(build_target("my", [])) == ["X=secret"]


@requires_age
def test_failed_nested_include_reports_file_and_chain(workdir: SimpleNamespace) -> None:
    # secrets.age is encrypted to a key we don't hold, reached via app -> secrets
    subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-f",
            str(workdir.root / "keyB"),
            "-N",
            "",
            "-q",
        ],
        check=True,
    )
    recipients = workdir.root / "recipB"
    recipients.write_text((workdir.root / "keyB.pub").read_text())
    (workdir.root / "my.emerg.env").write_text("@include app\n")
    workdir.write_env("app.env", "@include secrets\n")
    (workdir.data / "secrets.age").write_bytes(
        crypto.encrypt_bytes(b"S=1\n", recipients)
    )

    with pytest.raises(EmergenvError) as exc:
        build_target("my", [])
    message = str(exc.value)
    assert "secrets.age" in message  # which file
    assert "app -> secrets" in message  # how we got there


# --- cmd_build integration (via main) ---------------------------------------


@requires_age
def test_cmd_build_writes_target_env(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "my.emerg.env").write_text("A=1\n")
    result = run("build", "my")
    assert result.code == 0
    assert (workdir.root / "my.env").read_text().endswith("A=1\n")


@requires_age
def test_cmd_build_dot_writes_dotenv(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    assert run("build", "dot").code == 0
    assert (workdir.root / ".env").is_file()


@requires_age
def test_cmd_build_strips_target_extension(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "my.emerg.env").write_text("A=1\n")
    assert run("build", "my.emerg.env").code == 0
    assert (workdir.root / "my.env").is_file()


@requires_age
def test_cmd_build_no_source_flag(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "my.emerg.env").write_text("A=1\n")
    run("build", "my", "--no-source")
    assert "FROM" not in (workdir.root / "my.env").read_text()


@requires_age
@pytest.mark.usefixtures("workdir")
def test_cmd_build_rejects_dot_arg(run: Run) -> None:
    result = run("build", ".")
    assert result.code == 255
    assert "dot" in result.err


@requires_age
@pytest.mark.usefixtures("workdir")
def test_cmd_build_rejects_reserved_target(run: Run) -> None:
    result = run("build", "emergenv")
    assert result.code == 255
    assert "reserved" in result.err


@requires_age
def test_cmd_build_rejects_profile_with_slash(
    workdir: SimpleNamespace, run: Run
) -> None:
    (workdir.root / "my.emerg.env").write_text("A=1\n")
    result = run("build", "my", "--profile", "a/b")
    assert result.code == 255


# --- multi-profile & the six-layer search order -----------------------------


def test_multi_profile_last_profile_wins(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include db\n")
    bd.write_data("db.env", "X=global\n")
    bd.write_data("all/db.env", "X=all\n")
    bd.write_data("prod/db.env", "X=prod\n")
    out = build_target("my", ["all", "prod"])
    assert _active(out) == ["X=prod"]
    assert "# X=global" in out and "# X=all" in out


def test_profile_order_is_respected(bd: SimpleNamespace) -> None:
    # swapping the profile order swaps the winner
    bd.write_root("my.emerg.env", "@include db\n")
    bd.write_data("all/db.env", "X=all\n")
    bd.write_data("prod/db.env", "X=prod\n")
    assert _active(build_target("my", ["prod", "all"])) == ["X=all"]


def test_target_dir_overrides_profiles(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include db\n")
    bd.write_data("db.env", "X=global\n")
    bd.write_data("prod/db.env", "X=prod\n")
    bd.write_data("my/db.env", "X=target\n")
    assert _active(build_target("my", ["prod"])) == ["X=target"]


def test_full_six_layer_order(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include db\n")
    for rel, val in [
        ("db.env", "g"),
        ("all/db.env", "a"),
        ("prod/db.env", "p"),
        ("my/db.env", "t"),
        ("my/all/db.env", "ta"),
        ("my/prod/db.env", "tp"),
    ]:
        bd.write_data(rel, f"X={val}\n")
    # emergenv/my/prod/db is last in the order, so it wins
    assert _active(build_target("my", ["all", "prod"])) == ["X=tp"]


def test_profile_without_files_is_skipped(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include db\n")
    bd.write_data("db.env", "X=1\n")
    assert _active(build_target("my", ["ghost"])) == ["X=1"]


def test_keyref_respects_profile_order_and_provenance(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@K=secrets\n")
    bd.write_data("secrets.env", "K=global\n")
    bd.write_data("prod/secrets.env", "K=prod\n")
    out = build_target("my", ["prod"])
    assert _active(out) == ["K=prod"]
    assert "# FROM: emergenv/prod/secrets.env" in out


def test_nested_include_respects_profiles(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include outer\n")
    bd.write_data("outer.env", "@include inner\n")
    bd.write_data("inner.env", "X=global\n")
    bd.write_data("prod/inner.env", "X=prod\n")
    assert _active(build_target("my", ["prod"])) == ["X=prod"]


def test_dot_target_uses_dot_dir_for_overrides(bd: SimpleNamespace) -> None:
    bd.write_root("dot.emerg.env", "@include db\n")
    bd.write_data("db.env", "X=global\n")
    bd.write_data("dot/db.env", "X=dot\n")
    assert _active(build_target("dot", [])) == ["X=dot"]


# --- local file can carry its own directives --------------------------------


def test_local_can_use_includes(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n")
    bd.write_root("my.local.emerg.env", "@include extra\n")
    bd.write_data("extra.env", "B=2\n")
    assert _active(build_target("my", [])) == ["A=1", "B=2"]


def test_local_can_use_keyref(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n")
    bd.write_root("my.local.emerg.env", "@C=secrets\n")
    bd.write_data("secrets.env", "C=v\n")
    assert _active(build_target("my", [])) == ["A=1", "C=v"]


def test_local_include_overrides_base(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "X=base\n")
    bd.write_root("my.local.emerg.env", "@include over\n")
    bd.write_data("over.env", "X=local\n")
    out = build_target("my", [])
    assert _active(out) == ["X=local"]
    assert "# X=base" in out


def test_cwd_targets_are_plaintext_only(bd: SimpleNamespace) -> None:
    # A CWD .age is never a target; only the .env counts. The stray .age here
    # must be ignored, not preferred.
    bd.write_root("my.emerg.env", "A=plain\n")
    (bd.root / "my.emerg.age").write_bytes(b"not even valid age\n")
    (bd.root / "my.local.emerg.age").write_bytes(b"not even valid age\n")
    assert _active(build_target("my", [])) == ["A=plain"]


# --- base resolution: CWD shadows emergenv/ ---------------------------------


def test_base_falls_back_to_emergenv_env(bd: SimpleNamespace) -> None:
    # No CWD base; an emergenv/<target>.emerg.env is used instead.
    bd.write_data("my.emerg.env", "A=fromstore\n")
    out = build_target("my", [])
    assert "# FROM: emergenv/my.emerg.env" in out
    assert _active(out) == ["A=fromstore"]


@requires_age
def test_base_falls_back_to_emergenv_age(workdir: SimpleNamespace) -> None:
    recipients = workdir.data / "authorized_keys"
    (workdir.data / "my.emerg.age").write_bytes(
        crypto.encrypt_bytes(b"A=secret\n", recipients)
    )
    out = build_target("my", [])
    assert "# FROM: emergenv/my.emerg.age" in out
    assert _active(out) == ["A=secret"]


def test_emergenv_age_preferred_over_env(workdir: SimpleNamespace) -> None:
    # Inside emergenv/, .age wins over .env (the encrypted form is canonical).
    recipients = workdir.data / "authorized_keys"
    (workdir.data / "my.emerg.env").write_text("A=plain\n")
    (workdir.data / "my.emerg.age").write_bytes(
        crypto.encrypt_bytes(b"A=secret\n", recipients)
    )
    assert _active(build_target("my", [])) == ["A=secret"]


def test_cwd_base_shadows_emergenv_age(workdir: SimpleNamespace) -> None:
    # A plaintext base in the CWD wins over an encrypted one in emergenv/.
    recipients = workdir.data / "authorized_keys"
    (workdir.data / "my.emerg.age").write_bytes(
        crypto.encrypt_bytes(b"A=secret\n", recipients)
    )
    (workdir.root / "my.emerg.env").write_text("A=local\n")
    assert _active(build_target("my", [])) == ["A=local"]


def test_local_is_cwd_only_no_emergenv_fallback(bd: SimpleNamespace) -> None:
    # The .local layer is read only from the CWD; an emergenv/ copy is ignored.
    bd.write_root("my.emerg.env", "A=1\n")
    bd.write_data("my.local.emerg.env", "A=2\n")
    assert _active(build_target("my", [])) == ["A=1"]


def test_local_appends_to_emergenv_base(bd: SimpleNamespace) -> None:
    # CWD .local layers on top of an emergenv/ base.
    bd.write_data("my.emerg.env", "A=1\n")
    bd.write_root("my.local.emerg.env", "A=2\n")
    out = build_target("my", [])
    assert "# A=1" in out
    assert _active(out) == ["A=2"]


def test_nested_target_cwd_base(bd: SimpleNamespace) -> None:
    # '/' in the target name resolves relative to the CWD.
    (bd.root / "sub").mkdir()
    (bd.root / "sub" / "app.emerg.env").write_text("A=cwd\n")
    (bd.root / "sub" / "app.local.emerg.env").write_text("B=local\n")
    assert _active(build_target("sub/app", [])) == ["A=cwd", "B=local"]


def test_nested_target_emergenv_fallback(bd: SimpleNamespace) -> None:
    # '/' in the target name resolves under emergenv/ on fallback.
    bd.write_data("sub/app.emerg.env", "A=store\n")
    out = build_target("sub/app", [])
    assert "# FROM: emergenv/sub/app.emerg.env" in out
    assert _active(out) == ["A=store"]


# --- value & line edge cases ------------------------------------------------


def test_keyref_value_with_equals_signs(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@URL=secrets\n")
    bd.write_data("secrets.env", "URL=a=b=c\n")
    assert _active(build_target("my", [])) == ["URL=a=b=c"]


def test_keyref_bang_ignores_profiles(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@FOO=!secrets\n")
    bd.write_data("secrets.env", "FOO=v\n")
    bd.write_data("prod/secrets.env", "FOO=prof\n")
    assert _active(build_target("my", ["prod"])) == ["FOO=v"]


def test_quoted_value_preserved(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", 'A="hello world"\n')
    assert _active(build_target("my", [])) == ['A="hello world"']


def test_commented_source_assignment_is_left_alone(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "# A=1\nA=2\n")
    assert build_target("my", [], mark_source=False) == "# A=1\nA=2\n"


def test_indented_assignment_participates_in_last_wins(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n A=2\n")
    assert build_target("my", [], mark_source=False) == "# A=1\n A=2\n"


def test_indented_directive_is_recognised(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "  @include db\n")
    bd.write_data("db.env", "X=1\n")
    assert _active(build_target("my", [])) == ["X=1"]


def test_include_subpath(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include sub/db\n")
    bd.write_data("sub/db.env", "X=1\n")
    assert _active(build_target("my", [])) == ["X=1"]


def test_multiple_distinct_includes(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include a\n@include b\n")
    bd.write_data("a.env", "A=1\n")
    bd.write_data("b.env", "B=2\n")
    assert _active(build_target("my", [])) == ["A=1", "B=2"]


def test_include_then_explicit_assignment_wins(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include db\nX=top\n")
    bd.write_data("db.env", "X=db\n")
    out = build_target("my", [])
    assert _active(out) == ["X=top"]
    assert "# X=db" in out


def test_empty_base_produces_empty_output(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "")
    assert build_target("my", []) == ""


def test_source_markers_switch_back_and_forth(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\n@include db\nB=2\n")
    bd.write_data("db.env", "X=1\n")
    froms = [
        ln for ln in build_target("my", []).splitlines() if ln.startswith("# FROM:")
    ]
    assert froms == [
        "# FROM: my.emerg.env",
        "# FROM: emergenv/db.env",
        "# FROM: my.emerg.env",
    ]


def test_keyref_self_reference_is_a_cycle(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include a\n")
    bd.write_data("a.env", "@K=a\n")
    with pytest.raises(EmergenvError, match="cycle"):
        build_target("my", [])


@pytest.mark.parametrize(
    "directive",
    [
        "@include ../x",
        "@include /x",
        "@include !../x",
        "@FOO=emergenv",
        "@include emergenv/x",
        "@FOO=.",
    ],
)
def test_directive_name_validation(bd: SimpleNamespace, directive: str) -> None:
    bd.write_root("my.emerg.env", directive + "\n")
    with pytest.raises(EmergenvError):
        build_target("my", [])


# --- --bare -----------------------------------------------------------------


def test_bare_strips_comments_and_blanks(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "# a comment\n\nA=1\n\n# another\nB=2\n")
    assert build_target("my", [], bare=True) == "A=1\nB=2\n"


def test_bare_drops_overridden_lines_entirely(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "A=1\nA=2\n")
    assert build_target("my", [], bare=True) == "A=2\n"


def test_bare_has_no_source_markers(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "@include db\n")
    bd.write_data("db.env", "X=1\n")
    out = build_target("my", [], bare=True)
    assert "FROM" not in out
    assert out == "X=1\n"


def test_bare_keeps_export(bd: SimpleNamespace) -> None:
    bd.write_root("my.emerg.env", "export A=1\n")
    assert build_target("my", [], bare=True) == "export A=1\n"


# --- multiple --profile flags (merged in order) -----------------------------


def test_parse_profiles_flattens_repeated_and_comma() -> None:
    assert _parse_profiles(["all", "prod"]) == ["all", "prod"]
    assert _parse_profiles(["all,prod"]) == ["all", "prod"]
    assert _parse_profiles(["all", "prod,extra"]) == ["all", "prod", "extra"]
    assert _parse_profiles(None) == []


def test_parse_profiles_rejects_slash() -> None:
    with pytest.raises(EmergenvError):
        _parse_profiles(["a/b"])


@requires_age
def test_cmd_build_multiple_profile_flags(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "my.emerg.env").write_text("@include db\n")
    workdir.write_age("db.age", "X=global\n")
    workdir.write_age("all/db.age", "X=all\n")
    workdir.write_age("prod/db.age", "X=prod\n")
    assert run("build", "my", "--profile", "all", "--profile", "prod").code == 0
    # prod is last, so it wins
    assert (workdir.root / "my.env").read_text().rstrip().endswith("X=prod")


@requires_age
def test_cmd_build_bare_flag(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "my.emerg.env").write_text("# c\nA=1\n")
    run("build", "my", "--bare")
    assert (workdir.root / "my.env").read_text() == "A=1\n"


@requires_age
def test_cmd_build_output_to_file(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "my.emerg.env").write_text("A=1\n")
    result = run("build", "my", "--output", "custom.env")
    assert result.code == 0
    assert (workdir.root / "custom.env").read_text().endswith("A=1\n")
    assert not (workdir.root / "my.env").exists()  # default name not used


@requires_age
def test_build_from_subdir_discovers_emergenv(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    # emergenv/ (the include store) lives at the root; run from a subdir
    workdir.write_env("db.env", "X=1\n")
    sub = workdir.root / "svc"
    sub.mkdir()
    (sub / "my.emerg.env").write_text("@include db\n")  # target stays local (cwd)
    monkeypatch.chdir(sub)

    assert run("build", "my").code == 0
    assert "X=1" in (sub / "my.env").read_text()  # output local to the subdir
    assert not (workdir.root / "my.env").exists()  # not written at the root


@requires_age
def test_cmd_build_output_bad_path_reports_cleanly(
    workdir: SimpleNamespace, run: Run
) -> None:
    (workdir.root / "my.emerg.env").write_text("A=1\n")
    result = run("build", "my", "--output", "nope/out.env")  # parent dir missing
    assert result.code == 255
    assert "cannot write" in result.err
    assert "nope/out.env" in result.err


@requires_age
def test_cmd_build_output_dash_writes_stdout_only(
    workdir: SimpleNamespace, run: Run
) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    result = run("build", "dot", "--output", "-")
    assert result.code == 0
    assert "A=1" in result.out  # content on stdout
    assert "building:" not in result.out  # our progress logs are dropped entirely
    assert result.err == ""  # ...and stderr is left for errors only
    assert not (workdir.root / ".env").exists()  # nothing written to disk


# --- build logging ----------------------------------------------------------


def test_build_logs_target_local_imports(
    bd: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    bd.write_root("my.emerg.env", "@include db\n")
    bd.write_root("my.local.emerg.env", "L=1\n")
    bd.write_data("db.env", "X=1\n")
    build_target("my", [])
    lines = capsys.readouterr().out.splitlines()
    assert f"target: {bd.root / 'my.emerg.env'}" in lines
    assert f"local: {bd.root / 'my.local.emerg.env'}" in lines
    assert "importing: db.env" in lines


def test_build_target_line_uses_emergenv_fallback(
    bd: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    bd.write_data("my.emerg.env", "A=1\n")
    build_target("my", [])
    lines = capsys.readouterr().out.splitlines()
    assert f"target: {bd.root / 'emergenv' / 'my.emerg.env'}" in lines


def test_build_logs_no_local_or_import_lines_when_absent(
    bd: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    bd.write_root("my.emerg.env", "A=1\n")
    build_target("my", [])
    out = capsys.readouterr().out
    assert "target: " in out
    assert "local: " not in out
    assert "importing: " not in out


def test_build_imports_only_existing_files(
    bd: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    # db exists globally and under the target, but not for the profile; only the
    # two real files are logged - missing candidates are never mentioned.
    bd.write_root("my.emerg.env", "@include db\n")
    bd.write_data("db.env", "X=1\n")
    bd.write_data("my/db.env", "X=2\n")
    build_target("my", ["prod"])  # emergenv/prod/db.* does not exist
    imports = [
        ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("importing:")
    ]
    assert imports == ["importing: db.env", "importing: my/db.env"]


@requires_age
def test_build_command_logs_full_sequence(workdir: SimpleNamespace, run: Run) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n@include db\n")
    workdir.write_env("db.env", "X=1\n")
    (workdir.root / "dot.local.emerg.env").write_text("L=1\n")
    result = run("build", "dot", "--profile", "dev")
    assert result.code == 0
    out = result.out.splitlines()
    assert out[0] == "building: dot"
    assert out[1] == "profiles: dev"
    assert out[2] == f"emergenv: {workdir.data}"
    assert out[3] == f"target: {workdir.root / 'dot.emerg.env'}"
    assert out[4] == f"local: {workdir.root / 'dot.local.emerg.env'}"
    assert "importing: db.env" in out
    assert out[-1] == f"writing: {(workdir.root / '.env').resolve()}"


@requires_age
def test_build_command_omits_profiles_line_without_profiles(
    workdir: SimpleNamespace, run: Run
) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    assert "profiles:" not in run("build", "dot").out


@requires_age
def test_cmd_build_no_local_flag_ignores_override(
    workdir: SimpleNamespace, run: Run
) -> None:
    (workdir.root / "dot.emerg.env").write_text("A=1\n")
    (workdir.root / "dot.local.emerg.env").write_text("A=2\n")
    assert run("build", "dot", "--no-local").code == 0
    text = (workdir.root / ".env").read_text()
    assert "A=1" in text and "A=2" not in text
