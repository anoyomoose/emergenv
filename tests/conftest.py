"""Shared fixtures for the emergenv test suite.

Tests run the CLI in-process via ``main([...])`` so they can both exercise the
real ``age`` binary end-to-end and monkeypatch internals to force failure modes.

Crypto-dependent tests need both ``age`` and ``ssh-keygen``; throwaway SSH keys
are generated once per session. Decryption uses the ``EMERGENV_KEY`` override so
the real ``~/.ssh`` is never touched. Each test gets an isolated, chdir'd
``tmp_path`` and therefore a disposable ``emergenv/`` data directory.
"""

import shutil
import signal
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from emergenv import output
from emergenv.cli import main
from emergenv.crypto import encrypt_verified

Run = Callable[..., SimpleNamespace]

requires_age = pytest.mark.skipif(
    shutil.which("age") is None, reason="age binary not installed"
)


@pytest.fixture(autouse=True)
def _reset_process_state() -> Iterator[None]:
    """Isolate tests from process-global state that the CLI mutates: the output
    routing knobs (cmd_build) and the SIGPIPE disposition (main)."""
    saved = (output.log_mode, output.age_mode)
    sigpipe = signal.getsignal(signal.SIGPIPE) if hasattr(signal, "SIGPIPE") else None
    yield
    output.log_mode, output.age_mode = saved
    if sigpipe is not None:
        signal.signal(signal.SIGPIPE, sigpipe)


@pytest.fixture(scope="session")
def keypair(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    """A throwaway ed25519 SSH key generated once for the whole session."""
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen not installed")
    key_dir = tmp_path_factory.mktemp("keys")
    private = key_dir / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(private), "-N", "", "-q"],
        check=True,
    )
    public_path = key_dir / "id_ed25519.pub"
    return SimpleNamespace(
        private=private,
        public_path=public_path,
        public=public_path.read_text(),
    )


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> Run:
    """Invoke ``main`` and return its exit code plus captured stdout/stderr."""

    def _run(*args: str) -> SimpleNamespace:
        code = main(list(args))
        captured = capsys.readouterr()
        return SimpleNamespace(code=code, out=captured.out, err=captured.err)

    return _run


@pytest.fixture
def workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keypair: SimpleNamespace,
) -> SimpleNamespace:
    """An initialised working directory with a ready ``emergenv/`` data dir.

    Sets the cwd to a temp dir, points ``EMERGENV_KEY`` at the throwaway key,
    and seeds ``authorized_keys`` with its public half. Provides helpers to
    create plaintext and encrypted data files.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EMERGENV_KEY", str(keypair.private))

    data = tmp_path / "emergenv"
    data.mkdir()
    (data / "authorized_keys").write_text(keypair.public)

    def write_env(rel: str, text: str) -> Path:
        path = data / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_age(rel: str, text: str) -> Path:
        path = data / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypt_verified(text.encode(), path))
        return path

    return SimpleNamespace(
        root=tmp_path,
        data=data,
        keypair=keypair,
        write_env=write_env,
        write_age=write_age,
    )
