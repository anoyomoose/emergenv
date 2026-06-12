"""The _Trace recorder builds a provenance tree as nodes are recorded."""

from __future__ import annotations

from emergenv.merge import _Trace, _TraceNode  # noqa: F401


def test_records_top_level_assigns() -> None:
    t = _Trace()
    t.assign("A", "f.env", "A=1")
    t.assign("B", "f.env", "B=2")
    assert [c.key for c in t.root.children] == ["A", "B"]
    assert t.root.children[0].kind == "assign"


def test_directive_nesting() -> None:
    t = _Trace()
    t.enter("@include base")
    t.assign("A", "base.age", "A=1")
    t.leave()
    t.assign("B", "f.env", "B=2")
    assert t.root.children[0].kind == "directive"
    assert t.root.children[0].directive == "@include base"
    assert [c.key for c in t.root.children[0].children] == ["A"]
    assert t.root.children[1].key == "B"


def test_ignored_flag() -> None:
    t = _Trace()
    t.assign("A", "base.age", "A=1", ignored=True)
    assert t.root.children[0].ignored is True
