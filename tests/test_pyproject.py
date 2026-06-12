"""Tests for the ``emergenv pyproject`` build-chain runner.

The pure planner (``plan_builds`` and helpers) is exercised directly - it takes
config dicts and returns ``emergenv build`` argv vectors, so no subprocess or
``age`` is needed. Execution tests drive the CLI via ``main([...])`` and
monkeypatch ``subprocess.run`` to capture the commands without spawning builds.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from emergenv import EmergenvError
from emergenv.cli import main
from emergenv.pyproject import find_pyproject, normalize_list, plan_builds

# --------------------------------------------------------------------------- #
# normalize_list
# --------------------------------------------------------------------------- #


def test_normalize_list_wraps_a_single_string() -> None:
    assert normalize_list("a", key="profile") == ["a"]


def test_normalize_list_splits_a_comma_string() -> None:
    assert normalize_list("a,b", key="profile") == ["a", "b"]


def test_normalize_list_passes_through_a_list() -> None:
    assert normalize_list(["a", "b"], key="profile") == ["a", "b"]


def test_normalize_list_drops_empty_segments() -> None:
    assert normalize_list("a, ,b", key="profile") == ["a", "b"]


def test_normalize_list_rejects_a_non_string_element() -> None:
    with pytest.raises(EmergenvError):
        normalize_list([1], key="profile")


def test_normalize_list_rejects_a_wrong_type() -> None:
    with pytest.raises(EmergenvError):
        normalize_list(42, key="profile")


# --------------------------------------------------------------------------- #
# find_pyproject
# --------------------------------------------------------------------------- #


def test_find_pyproject_in_the_start_dir(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("")
    assert find_pyproject(tmp_path) == tmp_path / "pyproject.toml"


def test_find_pyproject_in_an_ancestor(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_pyproject(sub) == tmp_path / "pyproject.toml"


def test_find_pyproject_stops_at_the_git_root(tmp_path: Path) -> None:
    # pyproject above the git root must NOT be picked up.
    (tmp_path / "pyproject.toml").write_text("")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "sub"
    sub.mkdir()
    with pytest.raises(EmergenvError):
        find_pyproject(sub)


def test_find_pyproject_not_found(tmp_path: Path) -> None:
    with pytest.raises(EmergenvError):
        find_pyproject(tmp_path)


# --------------------------------------------------------------------------- #
# plan_builds
# --------------------------------------------------------------------------- #

NO_OVERRIDES = {
    "bare": None,
    "local": None,
    "source": None,
    "filter": None,
    "verbose": None,
    "trace": None,
}


def ov(**kw: object) -> dict:
    """A cli_overrides dict defaulting every flag to 'inherit' (None)."""
    return {**NO_OVERRIDES, **kw}


def plan(
    config: dict,
    profiles: tuple[str, ...] = (),
    groups: tuple[str, ...] = (),
    overrides: dict | None = None,
) -> list[list[str]]:
    return plan_builds(config, list(profiles), list(groups), overrides or ov())


def test_plan_minimal_inherits_global_defaults() -> None:
    config = {"local": False, "bare": True, "builds": [{"target": "dot"}]}
    assert plan(config) == [["emergenv", "build", "dot", "--bare", "--no-local"]]


def test_plan_build_overrides_global() -> None:
    config = {"bare": True, "builds": [{"target": "dot", "bare": False}]}
    assert plan(config) == [["emergenv", "build", "dot"]]


def test_plan_profile_force_ignores_cli() -> None:
    config = {"builds": [{"target": "dot", "profile": ["test"]}]}
    assert plan(config, profiles=("production",)) == [
        ["emergenv", "build", "dot", "--profile", "test"]
    ]


def test_plan_pre_post_wraps_cli_profile() -> None:
    config = {"builds": [{"target": "metrics", "profile_post": "eu"}]}
    assert plan(config, profiles=("production",)) == [
        ["emergenv", "build", "metrics", "--profile", "production,eu"]
    ]


def test_plan_dedupes_profiles() -> None:
    config = {"builds": [{"target": "m", "profile_post": ["production"]}]}
    assert plan(config, profiles=("production",)) == [
        ["emergenv", "build", "m", "--profile", "production"]
    ]


def test_plan_output_flag_trails() -> None:
    config = {"builds": [{"target": "env2", "output": "envs/bla.env"}]}
    assert plan(config) == [["emergenv", "build", "env2", "--output", "envs/bla.env"]]


def test_plan_trace_all() -> None:
    config = {"builds": [{"target": "dot", "trace": "*"}]}
    assert plan(config) == [["emergenv", "build", "dot", "--trace-all"]]


def test_plan_trace_list() -> None:
    config = {"builds": [{"target": "dot", "trace": ["A", "B"]}]}
    assert plan(config) == [["emergenv", "build", "dot", "--trace", "A,B"]]


def test_plan_flag_inversion() -> None:
    config = {
        "builds": [{"target": "dot", "source": False, "filter": False, "verbose": True}]
    }
    assert plan(config) == [
        ["emergenv", "build", "dot", "--no-source", "--no-filter", "--verbose"]
    ]


def test_plan_cli_override_forces_off() -> None:
    config = {"bare": True, "builds": [{"target": "dot"}]}
    assert plan(config, overrides=ov(bare=False)) == [["emergenv", "build", "dot"]]


def test_plan_cli_override_forces_on() -> None:
    config = {"builds": [{"target": "dot"}]}
    assert plan(config, overrides=ov(bare=True)) == [
        ["emergenv", "build", "dot", "--bare"]
    ]


def test_plan_cli_override_applies_to_every_build() -> None:
    config = {"builds": [{"target": "a", "bare": True}, {"target": "b", "bare": False}]}
    result = plan(config, overrides=ov(bare=False))
    assert result == [["emergenv", "build", "a"], ["emergenv", "build", "b"]]


def test_plan_cli_trace_replaces_toml() -> None:
    config = {"builds": [{"target": "dot", "trace": ["A"]}]}
    assert plan(config, overrides=ov(trace=["B"])) == [
        ["emergenv", "build", "dot", "--trace", "B"]
    ]


def test_plan_group_filter_selects_subset() -> None:
    config = {
        "builds": [
            {"target": "a", "group": "web"},
            {"target": "b"},
            {"target": "c", "group": "web"},
        ]
    }
    assert plan(config, groups=("web",)) == [
        ["emergenv", "build", "a"],
        ["emergenv", "build", "c"],
    ]


def test_plan_unknown_group_errors() -> None:
    config = {"builds": [{"target": "a", "group": "web"}]}
    with pytest.raises(EmergenvError):
        plan(config, groups=("missing",))


def test_plan_preserves_order() -> None:
    config = {"builds": [{"target": "a"}, {"target": "b"}, {"target": "c"}]}
    assert [argv[2] for argv in plan(config)] == ["a", "b", "c"]


def test_plan_missing_target_errors() -> None:
    with pytest.raises(EmergenvError):
        plan({"builds": [{}]})


def test_plan_global_target_errors() -> None:
    with pytest.raises(EmergenvError):
        plan({"target": "x", "builds": [{"target": "a"}]})


def test_plan_unknown_key_suggests() -> None:
    config = {"builds": [{"target": "a", "no_local": True}]}
    with pytest.raises(EmergenvError, match="local"):
        plan(config)


def test_plan_no_builds_errors() -> None:
    with pytest.raises(EmergenvError):
        plan({})
    with pytest.raises(EmergenvError):
        plan({"builds": []})


def test_plan_profile_force_with_pre_post_errors() -> None:
    config = {"builds": [{"target": "a", "profile": ["x"], "profile_post": ["y"]}]}
    with pytest.raises(EmergenvError, match="profile"):
        plan(config)


# --------------------------------------------------------------------------- #
# cmd_pyproject (execution; subprocess.run is stubbed so no build really runs)
#
# The command itself requires Python 3.11+ (tomllib), so these are skipped on
# older interpreters; the pure-planner tests above still run everywhere.
# --------------------------------------------------------------------------- #

requires_tomllib = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="the pyproject command requires Python 3.11+ (tomllib)",
)


def write_project(tmp_path: Path, toml_body: str) -> None:
    (tmp_path / "pyproject.toml").write_text(toml_body)


def fake_run_factory(
    calls: list, rc_sequence: list[int] | None = None
) -> Callable[..., SimpleNamespace]:
    seq = iter(rc_sequence) if rc_sequence is not None else None

    def fake_run(
        argv: list[str], cwd: str | None = None, **kwargs: object
    ) -> SimpleNamespace:
        calls.append((argv, cwd))
        return SimpleNamespace(returncode=next(seq) if seq is not None else 0)

    return fake_run


@requires_tomllib
def test_pyproject_runs_builds_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        """
        [tool.emergenv]
        local = false
        [[tool.emergenv.builds]]
        target = "env1"
        [[tool.emergenv.builds]]
        target = "env2"
        output = "envs/bla.env"
        """,
    )
    monkeypatch.chdir(tmp_path)
    calls: list = []
    monkeypatch.setattr("emergenv.cli.subprocess.run", fake_run_factory(calls))

    code = main(["pyproject", "--profile", "production"])

    assert code == 0
    assert [c[0] for c in calls] == [
        ["emergenv", "build", "env1", "--profile", "production", "--no-local"],
        [
            "emergenv",
            "build",
            "env2",
            "--profile",
            "production",
            "--no-local",
            "--output",
            "envs/bla.env",
        ],
    ]
    assert {c[1] for c in calls} == {str(tmp_path)}


@requires_tomllib
def test_pyproject_dry_run_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_project(tmp_path, '[[tool.emergenv.builds]]\ntarget = "env1"\n')
    monkeypatch.chdir(tmp_path)
    calls: list = []
    monkeypatch.setattr("emergenv.cli.subprocess.run", fake_run_factory(calls))

    code = main(["pyproject", "--dry-run"])

    assert code == 0
    assert calls == []
    assert "emergenv build env1" in capsys.readouterr().out


@requires_tomllib
def test_pyproject_stops_on_first_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        '[[tool.emergenv.builds]]\ntarget = "a"\n'
        '[[tool.emergenv.builds]]\ntarget = "b"\n'
        '[[tool.emergenv.builds]]\ntarget = "c"\n',
    )
    monkeypatch.chdir(tmp_path)
    calls: list = []
    monkeypatch.setattr(
        "emergenv.cli.subprocess.run", fake_run_factory(calls, rc_sequence=[0, 1, 0])
    )

    code = main(["pyproject"])

    assert code != 0
    assert [c[0][2] for c in calls] == ["a", "b"]  # 'c' never ran


@requires_tomllib
def test_pyproject_requires_emergenv_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_project(tmp_path, '[[tool.emergenv.builds]]\ntarget = "a"\n')
    monkeypatch.chdir(tmp_path)
    calls: list = []
    monkeypatch.setattr("emergenv.cli.subprocess.run", fake_run_factory(calls))
    monkeypatch.setattr(
        "emergenv.cli.shutil.which",
        lambda name: None if name == "emergenv" else f"/usr/bin/{name}",
    )

    code = main(["pyproject"])

    assert code != 0
    assert calls == []
    assert "not found on PATH" in capsys.readouterr().err


@requires_tomllib
def test_pyproject_without_config_file_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no pyproject.toml here or above (in /tmp)
    code = main(["pyproject"])
    assert code != 0
    assert "pyproject" in capsys.readouterr().err.lower()


@requires_tomllib
def test_pyproject_cli_no_bare_overrides_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        '[tool.emergenv]\nbare = true\n[[tool.emergenv.builds]]\ntarget = "a"\n',
    )
    monkeypatch.chdir(tmp_path)
    calls: list = []
    monkeypatch.setattr("emergenv.cli.subprocess.run", fake_run_factory(calls))

    code = main(["pyproject", "--no-bare"])

    assert code == 0
    assert calls[0][0] == ["emergenv", "build", "a"]
