"""Tests for path resolution and the data-directory containment guarantee."""

from pathlib import Path

import pytest

from emergenv import EmergenvError, paths


@pytest.fixture
def cd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Chdir into a temp dir with an empty ``emergenv/`` data dir."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "emergenv").mkdir()
    return tmp_path


def test_data_dir_is_under_cwd(cd: Path) -> None:
    assert paths.data_dir() == cd / "emergenv"


def test_require_data_dir_ok(cd: Path) -> None:
    assert paths.require_data_dir() == cd / "emergenv"


def test_require_data_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no emergenv/ created
    with pytest.raises(EmergenvError, match="emergenv.* init"):
        paths.require_data_dir()


# --- data_dir discovery (walk up to emergenv/, bounded by .git) -------------


def test_data_dir_found_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "emergenv").mkdir()
    monkeypatch.chdir(tmp_path)
    assert paths.data_dir() == tmp_path / "emergenv"


def test_data_dir_found_in_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "emergenv").mkdir()
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert paths.data_dir() == tmp_path / "emergenv"


def test_data_dir_found_at_git_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "emergenv").mkdir()
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert paths.data_dir() == tmp_path / "emergenv"  # found at the git root


def test_data_dir_does_not_cross_git_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # emergenv/ sits ABOVE a repo: discovery must NOT escape the repo to reach it
    (tmp_path / "emergenv").mkdir()
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert paths.data_dir() == sub / "emergenv"  # fell back to cwd, did not escape


def test_data_dir_not_found_falls_back_to_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = tmp_path / "x"
    here.mkdir()
    monkeypatch.chdir(here)
    assert paths.data_dir() == here / "emergenv"


# --- resolve_name: normalisation -------------------------------------------


@pytest.mark.parametrize(
    "name, expected_rel",
    [
        ("database", "database"),
        ("emergenv/database", "database"),  # leading prefix stripped
        ("database.age", "database"),  # trailing ext stripped
        ("database.env", "database"),
        ("emergenv/database.age", "database"),  # both stripped
        ("prod/database", "prod/database"),  # internal slash kept
        ("prod/database.env", "prod/database"),
        ("emergenv/prod/database.age", "prod/database"),
        ("my.config.env", "my.config"),  # only the trailing ext goes
        ("my.config", "my.config"),
        ("a.age.env", "a.age"),  # one ext only
    ],
)
def test_resolve_name_normalisation(cd: Path, name: str, expected_rel: str) -> None:
    assert paths.resolve_name(name) == (cd / "emergenv" / expected_rel).resolve()


# --- resolve_name: rejecting bad input -------------------------------------


@pytest.mark.usefixtures("cd")
@pytest.mark.parametrize(
    "name",
    [
        "../etc/passwd",
        "../../etc/passwd",
        "/etc/passwd",
        "foo/../../bar",
        "..",
        "emergenv/../secret",
    ],
)
def test_resolve_name_rejects_escapes(name: str) -> None:
    with pytest.raises(EmergenvError):
        paths.resolve_name(name)


@pytest.mark.usefixtures("cd")
@pytest.mark.parametrize("name", ["", "   ", "emergenv/", ".age", ".env"])
def test_resolve_name_rejects_empty(name: str) -> None:
    with pytest.raises(EmergenvError):
        paths.resolve_name(name)


def test_resolve_name_rejects_symlink_escape(cd: Path) -> None:
    """A symlink inside emergenv/ that points outside must not be a way out."""
    outside = cd / "outside"
    outside.mkdir()
    (cd / "emergenv" / "link").symlink_to(outside)
    with pytest.raises(EmergenvError, match="escapes"):
        paths.resolve_name("link/secret")


@pytest.mark.usefixtures("cd")
@pytest.mark.parametrize("name", [".", "emergenv/."])
def test_resolve_name_dot_suggests_dot(name: str) -> None:
    """'.' is invalid and the error points users at the 'dot' target."""
    with pytest.raises(EmergenvError, match="dot"):
        paths.resolve_name(name)


@pytest.mark.usefixtures("cd")
@pytest.mark.parametrize(
    "name",
    [
        "emergenv",
        "emergenv.age",
        "emergenv.env",
        "emergenv/emergenv",
        "emergenv/emergenv/x",
    ],
)
def test_resolve_name_reserves_emergenv(name: str) -> None:
    """A first component of 'emergenv' is reserved (ambiguous with the prefix)."""
    with pytest.raises(EmergenvError, match="reserved"):
        paths.resolve_name(name)


# --- validate_component (shared by <name> and <target>) ---------------------


@pytest.mark.parametrize(
    "bad", ["", ".", "/abs", "a/../b", "..", "emergenv", "emergenv/x"]
)
def test_validate_component_rejects(bad: str) -> None:
    with pytest.raises(EmergenvError):
        paths.validate_component(bad, kind="target")


def test_validate_component_uses_kind_in_message() -> None:
    with pytest.raises(EmergenvError, match="target"):
        paths.validate_component(".", kind="target")


@pytest.mark.parametrize("ok", ["my", "dot", "prod/database", "my.config"])
def test_validate_component_accepts(ok: str) -> None:
    paths.validate_component(ok)  # must not raise


# --- helpers ----------------------------------------------------------------


def test_nearest_authorized_keys_walks_up(cd: Path) -> None:
    data = cd / "emergenv"
    (data / "authorized_keys").write_text("base\n")
    prod = data / "prod"
    prod.mkdir()
    # no prod/authorized_keys yet -> nearest is the base
    assert paths.nearest_authorized_keys(prod / "db.age") == data / "authorized_keys"
    # add one in prod/ -> now it wins for files in prod/
    (prod / "authorized_keys").write_text("prod\n")
    assert paths.nearest_authorized_keys(prod / "db.age") == prod / "authorized_keys"
    # files directly in emergenv/ still use the base
    assert paths.nearest_authorized_keys(data / "db.age") == data / "authorized_keys"


def test_nearest_authorized_keys_missing(cd: Path) -> None:
    with pytest.raises(EmergenvError, match="no authorized_keys"):
        paths.nearest_authorized_keys((cd / "emergenv") / "db.age")


@pytest.mark.usefixtures("cd")
def test_age_and_env_file_helpers() -> None:
    base = paths.resolve_name("prod/database")
    assert paths.age_file(base).name == "database.age"
    assert paths.env_file(base).name == "database.env"
    assert paths.age_file(base).parent == base.parent


@pytest.mark.usefixtures("cd")
def test_age_file_keeps_dotted_stem() -> None:
    base = paths.resolve_name("my.config")
    assert paths.age_file(base).name == "my.config.age"


# --- iter_pairs -------------------------------------------------------------


def test_iter_pairs_groups_and_recurses(cd: Path) -> None:
    data = cd / "emergenv"
    (data / "alpha.age").write_bytes(b"x")
    (data / "alpha.env").write_text("x")
    (data / "beta.env").write_text("y")
    (data / "prod").mkdir()
    (data / "prod" / "gamma.age").write_bytes(b"z")
    # non age/env files must be ignored
    (data / "authorized_keys").write_text("keys")
    (data / ".gitignore").write_text("*.env")

    pairs = {p["name"]: p for p in paths.iter_pairs()}

    assert set(pairs) == {"alpha", "beta", "prod/gamma"}
    assert pairs["alpha"]["age"] and pairs["alpha"]["env"]
    assert pairs["beta"]["age"] is None and pairs["beta"]["env"]
    assert pairs["prod/gamma"]["age"] and pairs["prod/gamma"]["env"] is None


def test_iter_pairs_sorted(cd: Path) -> None:
    data = cd / "emergenv"
    for name in ("c", "a", "b"):
        (data / f"{name}.env").write_text("x")
    assert [p["name"] for p in paths.iter_pairs()] == ["a", "b", "c"]


@pytest.mark.usefixtures("cd")
def test_iter_pairs_empty() -> None:
    assert paths.iter_pairs() == []
