"""Tests for the ``status`` and ``clean`` commands."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

from conftest import Run, requires_age

pytestmark = requires_age


def _keygen(path: Path) -> Path:
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(path), "-N", "", "-q"], check=True
    )
    return path


# --- status -----------------------------------------------------------------


def test_status_match(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    workdir.write_env("database.env", "FOO=bar\n")
    result = run("status")
    assert "database AGE+ENV MATCH" in result.out
    assert result.code == 1


def test_status_mismatch(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    workdir.write_env("database.env", "FOO=different\n")
    result = run("status")
    assert "database AGE+ENV MISMATCH" in result.out
    assert result.code == 2


def test_status_age_only(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    result = run("status")
    assert result.out.strip() == "database AGE"
    assert result.code == 0


def test_status_env_only(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("database.env", "FOO=bar\n")
    result = run("status")
    assert result.out.strip() == "database ENV"
    assert result.code == 1


def test_status_recurses(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("prod/database.env", "FOO=bar\n")
    assert "prod/database ENV" in run("status").out


def test_status_empty(workdir: SimpleNamespace, run: Run) -> None:
    result = run("status")
    assert "no files found" in result.out
    assert result.code == 0


def test_status_ignores_metadata_files(workdir: SimpleNamespace, run: Run) -> None:
    # authorized_keys exists from the fixture; .gitignore too once written.
    (workdir.data / ".gitignore").write_text("*.env\n")
    out = run("status").out
    assert "authorized_keys" not in out
    assert "gitignore" not in out


def test_status_reports_error_and_continues(workdir: SimpleNamespace, run: Run) -> None:
    """A corrupt .age is reported as ERROR but the listing continues."""
    (workdir.data / "broken.age").write_bytes(b"not a real age file")
    workdir.write_env("broken.env", "FOO=bar\n")
    workdir.write_age("ok.age", "OK=1\n")
    result = run("status")
    assert "broken ERROR" in result.out
    assert "ok AGE" in result.out  # listing continued past the error
    assert result.code == 3  # highest code seen


def test_status_exit_is_highest_code(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("a.age", "A=1\n")
    workdir.write_env("a.env", "A=1\n")  # match -> 1
    workdir.write_age("b.age", "B=1\n")
    workdir.write_env("b.env", "B=2\n")  # mismatch -> 2
    assert run("status").code == 2


# --- status: recipient pre-flight -------------------------------------------


def test_status_aborts_when_locked_out_of_a_set(
    workdir: SimpleNamespace, run: Run
) -> None:
    # a subdir authorized_keys that lists ONLY some other key excludes you.
    _keygen(workdir.root / "keyB")
    prod = workdir.data / "prod"
    prod.mkdir()
    (prod / "authorized_keys").write_text((workdir.root / "keyB.pub").read_text())
    workdir.write_age("ok.age", "OK=1\n")  # would otherwise list as AGE

    result = run("status")
    assert result.code == 3
    assert "prod/authorized_keys" in result.err
    assert "ok" not in result.out  # aborted before the listing


def test_status_ok_when_recipient_in_every_set(
    workdir: SimpleNamespace, run: Run
) -> None:
    # subdir set includes BOTH you and another key -> you are still a recipient.
    _keygen(workdir.root / "keyB")
    prod = workdir.data / "prod"
    prod.mkdir()
    (prod / "authorized_keys").write_text(
        workdir.keypair.public + (workdir.root / "keyB.pub").read_text()
    )
    workdir.write_age("prod/db.age", "X=1\n")

    result = run("status")
    assert result.code == 0
    assert "prod/db AGE" in result.out
    assert result.err == ""  # silent pre-flight


def test_status_aborts_on_empty_authorized_keys(
    workdir: SimpleNamespace, run: Run
) -> None:
    (workdir.data / "authorized_keys").write_text("")  # nobody is a recipient
    result = run("status")
    assert result.code == 3
    assert "authorized_keys" in result.err


# --- clean ------------------------------------------------------------------


def test_clean_removes_matching_env(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    workdir.write_env("database.env", "FOO=bar\n")
    result = run("clean")
    assert result.code == 0
    assert not (workdir.data / "database.env").exists()
    assert (workdir.data / "database.age").exists()  # age never touched


def test_clean_keeps_differing_env(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    workdir.write_env("database.env", "FOO=different\n")
    result = run("clean")
    assert (workdir.data / "database.env").exists()
    assert "kept" in result.out


def test_clean_ignores_env_without_age(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_env("database.env", "FOO=bar\n")
    run("clean")
    assert (workdir.data / "database.env").exists()


def test_clean_nothing_to_do(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("database.age", "FOO=bar\n")
    assert "nothing to clean" in run("clean").out


def test_clean_recurses(workdir: SimpleNamespace, run: Run) -> None:
    workdir.write_age("prod/database.age", "FOO=bar\n")
    workdir.write_env("prod/database.env", "FOO=bar\n")
    run("clean")
    assert not (workdir.data / "prod" / "database.env").exists()


def test_clean_does_not_touch_files_outside_emergenv(
    workdir: SimpleNamespace, run: Run
) -> None:
    outside = workdir.root / "database.env"  # same name, outside the data dir
    outside.write_text("FOO=bar\n")
    workdir.write_age("database.age", "FOO=bar\n")
    workdir.write_env("database.env", "FOO=bar\n")
    run("clean")
    assert not (workdir.data / "database.env").exists()  # inner removed
    assert outside.exists()  # outer untouched
