"""The expand package must stay dependency-free (stdlib only, never emergenv.*),
so it can be spun off into its own library unchanged."""

from __future__ import annotations

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "src" / "emergenv" / "expand"


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import within the package: allowed.
            if node.level == 0 and node.module is not None:
                names.add(node.module)
    return names


def test_expand_package_imports_stdlib_only() -> None:
    offenders: dict[str, set[str]] = {}
    for py in PACKAGE.rglob("*.py"):
        bad = {m for m in _imported_modules(py) if m.split(".")[0] == "emergenv"}
        if bad:
            offenders[py.name] = bad
    assert offenders == {}, f"expand/ must not import emergenv.*: {offenders}"
