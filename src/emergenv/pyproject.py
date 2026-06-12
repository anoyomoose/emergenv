"""``pyproject.toml`` build-chain runner for emergenv.

Reads ``[tool.emergenv]`` build definitions and turns them into a list of
``emergenv build`` argv vectors, which :func:`emergenv.cli.cmd_pyproject` then
runs one by one. The planning is pure (config in, argv out) so it is fully
testable without spawning a build; only the thin execution loop in the CLI
shells out.

Parsing uses the standard-library ``tomllib`` (Python 3.11+); it is imported
lazily in the CLI so the rest of emergenv keeps working on 3.9/3.10.
"""

from __future__ import annotations

from pathlib import Path

from . import EmergenvError
from .paths import validate_component

# Boolean keys and their built-in defaults (chosen to equal `build`'s own
# defaults, so an unset key resolves identically to the command's default).
_BOOL_DEFAULTS = {
    "bare": False,
    "local": True,
    "source": True,
    "filter": True,
    "verbose": False,
}
# Keys valid in the global [tool.emergenv] table.
_GLOBAL_KEYS = set(_BOOL_DEFAULTS) | {"trace", "profile", "profile_pre", "profile_post"}
# Keys valid only per-build.
_BUILD_ONLY_KEYS = {"target", "output", "group"}
_BUILD_KEYS = _GLOBAL_KEYS | _BUILD_ONLY_KEYS

# Likely mistakes mapped to the right spelling.
_SUGGESTIONS = {
    "no_local": "local = false",
    "no-local": "local = false",
    "no_source": "source = false",
    "no-source": "source = false",
    "no_filter": "filter = false",
    "no-filter": "filter = false",
    "profiles": "profile (or profile_pre / profile_post)",
    "name": "group",
}


def normalize_list(value: object, *, key: str) -> list[str]:
    """Coerce a scalar-or-list config value into a list of strings.

    A string is split on ``,`` (commas are illegal inside profile/group tokens,
    so this is unambiguous and matches the CLI's comma-separated form); a list
    has each element validated as a string and likewise comma-split. Empty
    segments are dropped. Any other type, or a non-string list element, raises.
    """
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise EmergenvError(
            f"'{key}' must be a string or a list of strings, not "
            f"{type(value).__name__}"
        )
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise EmergenvError(f"'{key}' must contain only strings; got {item!r}")
        for segment in item.split(","):
            segment = segment.strip()
            if segment:
                result.append(segment)
    return result


def load_emergenv_config(path: Path) -> dict:
    """Return the ``[tool.emergenv]`` table from ``path`` (``{}`` if absent).

    Uses the standard-library ``tomllib`` (Python 3.11+); the caller gates the
    version and surfaces a clean message, so the import here is unguarded.
    """
    import tomllib

    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    tool = data.get("tool", {})
    config = tool.get("emergenv", {}) if isinstance(tool, dict) else {}
    return config if isinstance(config, dict) else {}


def find_pyproject(start: Path) -> Path:
    """Locate the nearest ``pyproject.toml`` at or above ``start``.

    Walks up ancestors, stopping at the git-root boundary (a directory holding
    ``.git``) or the filesystem root - the same barrier used to locate
    ``emergenv/`` - so a parent project's file is never picked up. Raises if none
    is found before the boundary.
    """
    current = start
    while True:
        candidate = current / "pyproject.toml"
        if candidate.is_file():
            return candidate
        if (current / ".git").exists():  # git root: do not search above it
            break
        parent = current.parent
        if parent == current:  # filesystem root
            break
        current = parent
    raise EmergenvError("no pyproject.toml found (searched up to the git root)")


def _check_keys(table: dict, allowed: set[str], *, where: str) -> None:
    for key in table:
        if key in allowed:
            continue
        hint = _SUGGESTIONS.get(key)
        suffix = f" (did you mean {hint}?)" if hint else ""
        raise EmergenvError(f"unknown key '{key}' in {where}{suffix}")


def _as_bool(value: object, key: str) -> bool:
    if not isinstance(value, bool):
        raise EmergenvError(f"'{key}' must be true or false, not {value!r}")
    return value


def _resolve_bool(name: str, global_t: dict, build: dict, overrides: dict) -> bool:
    value = _BOOL_DEFAULTS[name]
    if name in global_t:
        value = _as_bool(global_t[name], name)
    if name in build:
        value = _as_bool(build[name], name)
    if overrides.get(name) is not None:
        value = overrides[name]
    return value


def _resolve_trace(
    global_t: dict, build: dict, overrides: dict
) -> str | list[str] | None:
    if overrides.get("trace") is not None:
        raw: object = overrides["trace"]
    elif "trace" in build:
        raw = build["trace"]
    elif "trace" in global_t:
        raw = global_t["trace"]
    else:
        return None
    if raw == "*":
        return "*"
    return normalize_list(raw, key="trace") or None


def _profile_part(key: str, global_t: dict, build: dict) -> list[str]:
    if key in build:
        return normalize_list(build[key], key=key)
    if key in global_t:
        return normalize_list(global_t[key], key=key)
    return []


def _resolve_profiles(
    global_t: dict, build: dict, cli_profiles: list[str]
) -> list[str]:
    for table, where in ((global_t, "[tool.emergenv]"), (build, "a build")):
        if "profile" in table and ("profile_pre" in table or "profile_post" in table):
            raise EmergenvError(
                f"'profile' forces the list in {where}; "
                "remove profile_pre/profile_post or remove profile"
            )
    if "profile" in build:
        effective = normalize_list(build["profile"], key="profile")
    elif "profile" in global_t:
        effective = normalize_list(global_t["profile"], key="profile")
    else:
        pre = _profile_part("profile_pre", global_t, build)
        post = _profile_part("profile_post", global_t, build)
        effective = pre + list(cli_profiles) + post
    seen: set[str] = set()
    deduped: list[str] = []
    for profile in effective:
        if profile not in seen:
            seen.add(profile)
            deduped.append(profile)
    return deduped


def plan_builds(
    config: dict,
    cli_profiles: list[str],
    cli_groups: list[str],
    cli_overrides: dict,
) -> list[list[str]]:
    """Resolve ``[tool.emergenv]`` config into ordered ``emergenv build`` argv.

    Pure: validates the config, merges global defaults with each build and the
    CLI overrides, resolves the profile axis (§profile-resolution) and group
    selection, and assembles one ``emergenv build …`` vector per selected build,
    in file order. Raises :class:`EmergenvError` on any config problem.
    """
    builds = config.get("builds")
    if not builds:
        raise EmergenvError(
            "no builds defined in [tool.emergenv] (add a [[tool.emergenv.builds]] entry)"
        )

    global_t = {k: v for k, v in config.items() if k != "builds"}
    for key in _BUILD_ONLY_KEYS:
        if key in global_t:
            raise EmergenvError(f"'{key}' is per-build only, not a global default")
    _check_keys(global_t, _GLOBAL_KEYS, where="[tool.emergenv]")

    planned: list[tuple[set[str], list[str]]] = []
    for index, build in enumerate(builds, start=1):
        if not isinstance(build, dict):
            raise EmergenvError(f"[[tool.emergenv.builds]] #{index} must be a table")
        _check_keys(build, _BUILD_KEYS, where=f"build #{index}")

        target = build.get("target")
        if target is None:
            raise EmergenvError(f"build #{index} has no 'target'")
        if not isinstance(target, str):
            raise EmergenvError(f"'target' must be a string, not {target!r}")
        validate_component(target.strip(), kind="target")

        output = build.get("output")
        if output is not None and not isinstance(output, str):
            raise EmergenvError(f"'output' must be a string, not {output!r}")

        profiles = _resolve_profiles(global_t, build, cli_profiles)
        trace = _resolve_trace(global_t, build, cli_overrides)
        groups = (
            set(normalize_list(build["group"], key="group"))
            if "group" in build
            else set()
        )

        argv = ["emergenv", "build", target]
        if profiles:
            argv += ["--profile", ",".join(profiles)]
        if _resolve_bool("bare", global_t, build, cli_overrides):
            argv += ["--bare"]
        if not _resolve_bool("local", global_t, build, cli_overrides):
            argv += ["--no-local"]
        if not _resolve_bool("source", global_t, build, cli_overrides):
            argv += ["--no-source"]
        if not _resolve_bool("filter", global_t, build, cli_overrides):
            argv += ["--no-filter"]
        if _resolve_bool("verbose", global_t, build, cli_overrides):
            argv += ["--verbose"]
        if trace == "*":
            argv += ["--trace-all"]
        elif trace:
            argv += ["--trace", ",".join(trace)]
        if output:
            argv += ["--output", output]

        planned.append((groups, argv))

    if cli_groups:
        requested = set(cli_groups)
        defined = set().union(*(groups for groups, _ in planned)) if planned else set()
        missing = sorted(requested - defined)
        if missing:
            raise EmergenvError(f"no builds in group(s): {', '.join(missing)}")
        return [argv for groups, argv in planned if groups & requested]
    return [argv for _, argv in planned]
