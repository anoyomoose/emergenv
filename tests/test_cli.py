"""Tests for CLI plumbing: age gating, --version/--help, errors, exit codes."""

import signal
from pathlib import Path

import pytest
from conftest import Run, requires_age

from emergenv import __version__, cli
from emergenv.cli import main


def test_age_gate_blocks_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "age_check", lambda: False)
    assert main(["status"]) == 255  # operational error, not a graded status code
    assert "age" in capsys.readouterr().err


def test_version_bypasses_age_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "age_check", lambda: False)
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_bypasses_age_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "age_check", lambda: False)
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "usage: emergenv" in capsys.readouterr().out


def test_no_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 254


def test_unknown_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 254


@requires_age
def test_errors_go_to_stderr_not_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)  # no emergenv/ -> require_data_dir fails
    code = main(["status"])
    captured = capsys.readouterr()
    assert code == 255  # operational error, never a graded status code (0-3)
    assert captured.out == ""
    assert captured.err.startswith("error:")


@requires_age
def test_missing_data_dir_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    main(["status"])
    assert "init" in capsys.readouterr().err


def test_main_sets_default_sigpipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: Run
) -> None:
    if not hasattr(signal, "SIGPIPE"):
        pytest.skip("no SIGPIPE on this platform")
    monkeypatch.chdir(tmp_path)
    run("status")  # any command: SIGPIPE is set at the top of main() regardless
    assert signal.getsignal(signal.SIGPIPE) == signal.SIG_DFL
