"""Tests for internal output routing: log()/error() destinations and how the
age subprocess's stderr is handled. (age stdout is always captured - it's the
data - so only stderr is routed.)"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from conftest import requires_age

from emergenv import EmergenvError, crypto, output
from emergenv.cli import error
from emergenv.output import colourize, log

# --- log() / error() --------------------------------------------------------


def test_error_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    error("boom")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: boom" in captured.err


def test_log_defaults_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    log("hi")
    captured = capsys.readouterr()
    assert "hi" in captured.out
    assert captured.err == ""


def test_log_off_is_silent(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(output, "log_mode", output.LOG_OFF)
    log("hi")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_log_can_route_to_stderr(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(output, "log_mode", output.LOG_STDERR)
    log("hi")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "hi" in captured.err


# --- age stderr routing -----------------------------------------------------


def _ciphertext_then_wrong_key(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> bytes:
    """Encrypt something, then point EMERGENV_KEY at a key that can't decrypt it."""
    ciphertext = crypto.encrypt_verified(b"x\n", workdir.data / "x.age")
    wrong = workdir.root / "wrong"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(wrong), "-N", "", "-q"], check=True
    )
    monkeypatch.setenv("EMERGENV_KEY", str(wrong))
    return ciphertext


@requires_age
def test_age_capture_surfaces_detail_in_error(
    workdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    ciphertext = _ciphertext_then_wrong_key(workdir, monkeypatch)  # default AGE_CAPTURE
    with pytest.raises(EmergenvError) as exc:
        crypto.decrypt_bytes(ciphertext)
    # captured age stderr is appended to our message
    assert str(exc.value) != "age failed"
    assert "age failed:" in str(exc.value)


@requires_age
def test_age_off_discards_stderr(
    workdir: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    ciphertext = _ciphertext_then_wrong_key(workdir, monkeypatch)
    monkeypatch.setattr(output, "age_mode", output.AGE_OFF)
    capfd.readouterr()  # flush setup output
    with pytest.raises(EmergenvError):
        crypto.decrypt_bytes(ciphertext)
    assert capfd.readouterr().err == ""  # age's stderr was discarded


@requires_age
def test_age_stderr_passthrough(
    workdir: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    ciphertext = _ciphertext_then_wrong_key(workdir, monkeypatch)
    monkeypatch.setattr(output, "age_mode", output.AGE_STDERR)
    capfd.readouterr()  # flush setup output
    with pytest.raises(EmergenvError):
        crypto.decrypt_bytes(ciphertext)
    assert capfd.readouterr().err != ""  # age wrote diagnostics straight to our stderr


# --- colourize() ------------------------------------------------------------


def test_no_colour_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert colourize("AGE", "green") == "AGE"


def test_no_colour_when_no_color_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert colourize("AGE", "green") == "AGE"


def test_colour_when_tty_and_no_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert colourize("AGE", "red") == "\033[31mAGE\033[0m"
