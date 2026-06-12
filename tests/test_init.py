"""Tests for the ``init`` command.

``cmd_init`` is called directly so these don't depend on the ``age`` binary
(the age gate lives in ``main`` and is tested separately). ``$HOME`` is faked so
the real ``~/.ssh`` is never read.
"""

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from emergenv import EmergenvError
from emergenv.cli import cmd_init


@pytest.fixture
def init_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.chdir(tmp_path)
    ssh = tmp_path / "home" / ".ssh"
    ssh.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return SimpleNamespace(root=tmp_path, ssh=ssh)


def _init() -> int:
    return cmd_init(argparse.Namespace())


def test_init_creates_scaffold(init_env: SimpleNamespace) -> None:
    assert _init() == 0
    data = init_env.root / "emergenv"
    assert data.is_dir()
    assert (data / ".gitignore").is_file()
    assert (data / "authorized_keys").is_file()


def test_init_gitignore_excludes_env(init_env: SimpleNamespace) -> None:
    _init()
    assert "*.env" in (init_env.root / "emergenv" / ".gitignore").read_text()


def test_init_seeds_both_ssh_keys(init_env: SimpleNamespace) -> None:
    (init_env.ssh / "id_ed25519.pub").write_text("ssh-ed25519 AAAAED25519 a\n")
    (init_env.ssh / "id_rsa.pub").write_text("ssh-rsa AAAARSA b\n")
    _init()
    content = (init_env.root / "emergenv" / "authorized_keys").read_text()
    assert "ssh-ed25519 AAAAED25519" in content
    assert "ssh-rsa AAAARSA" in content


def test_init_seeds_one_ssh_key(init_env: SimpleNamespace) -> None:
    (init_env.ssh / "id_ed25519.pub").write_text("ssh-ed25519 AAAAONLY a\n")
    _init()
    content = (init_env.root / "emergenv" / "authorized_keys").read_text()
    assert content.count("ssh-") == 1


def test_init_empty_authorized_keys_when_no_ssh_keys(init_env: SimpleNamespace) -> None:
    _init()
    assert (init_env.root / "emergenv" / "authorized_keys").read_text() == ""


def test_init_refuses_when_dir_exists(init_env: SimpleNamespace) -> None:
    (init_env.root / "emergenv").mkdir()
    with pytest.raises(EmergenvError, match="already exists"):
        _init()
