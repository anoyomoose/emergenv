from __future__ import annotations

import pytest

from emergenv.expand.errors import ExpansionError
from emergenv.expand.scanner import ARITH, LITERAL, VAR, scan


def kinds(template: str) -> list[tuple[str, str]]:
    return [(s.kind, s.text) for s in scan(template)]


# ---------------------------------------------------------------------------
# Plan tests (from Task 4 spec)
# ---------------------------------------------------------------------------


def test_plain_literal() -> None:
    assert kinds("hello") == [(LITERAL, "hello")]


def test_simple_var() -> None:
    assert kinds("a${B}c") == [(LITERAL, "a"), (VAR, "B"), (LITERAL, "c")]


def test_nested_braces_preserved_in_body() -> None:
    assert kinds("${A:-${B}}") == [(VAR, "A:-${B}")]


def test_arithmetic_segment() -> None:
    assert kinds("p$(( 1 + 2 ))q") == [
        (LITERAL, "p"),
        (ARITH, " 1 + 2 "),
        (LITERAL, "q"),
    ]


def test_dollar_escape_and_lone_dollar() -> None:
    assert kinds("$$") == [(LITERAL, "$")]
    assert kinds("$${B}") == [(LITERAL, "${B}")]  # $$ -> literal $, then literal {B}
    assert kinds("price$5") == [(LITERAL, "price$5")]  # $ not before { or (( is literal


def test_unterminated_brace_errors() -> None:
    with pytest.raises(ExpansionError, match="unterminated"):
        scan("${B")


def test_unterminated_arith_errors() -> None:
    with pytest.raises(ExpansionError, match="unterminated"):
        scan("$(( 1 + 2 ")


# ---------------------------------------------------------------------------
# Additional tests (mandate extras)
# ---------------------------------------------------------------------------


def test_deeply_nested_var_body_intact() -> None:
    # Three levels deep: body must be returned verbatim including inner ${...}
    assert kinds("${A:-${B:-${C}}}") == [(VAR, "A:-${B:-${C}}")]


def test_arithmetic_with_inner_parens() -> None:
    # Inner grouping parens must not confuse the ))-matching
    assert kinds("$(( (1 + 2) * 3 ))") == [(ARITH, " (1 + 2) * 3 ")]


def test_adjacent_var_segments_no_literal_between() -> None:
    assert kinds("${A}${B}") == [(VAR, "A"), (VAR, "B")]


def test_adjacent_var_and_arith_segments() -> None:
    assert kinds("${A}$(( 1 ))") == [(VAR, "A"), (ARITH, " 1 ")]


def test_double_dollar_runs() -> None:
    # $$$$ -> literal $$
    assert kinds("$$$$") == [(LITERAL, "$$")]
    # a$$b -> a$b  (literal a, $$ -> $, literal b)
    assert kinds("a$$b") == [(LITERAL, "a$b")]
    # $$( with space after -> $$ -> literal $, then literal (
    assert kinds("$$( ") == [(LITERAL, "$( ")]


def test_lone_trailing_dollar() -> None:
    # Template ending in a bare $ keeps it literal
    assert kinds("abc$") == [(LITERAL, "abc$")]


def test_dollar_followed_by_non_special_char() -> None:
    # $ followed by a letter (not {) is literal
    assert kinds("$x") == [(LITERAL, "$x")]
    # $ followed by a digit is literal
    assert kinds("$9") == [(LITERAL, "$9")]


def test_literal_brace_outside_expansion() -> None:
    # A lone } outside any ${...} is just a literal character
    assert kinds("a}b") == [(LITERAL, "a}b")]


def test_literal_paren_outside_arithmetic() -> None:
    # A ) outside arithmetic is just a literal character
    assert kinds("a)b") == [(LITERAL, "a)b")]


def test_unterminated_single_closing_paren() -> None:
    # $(( 1 ) — only one closing paren, not two — is unterminated
    with pytest.raises(ExpansionError, match="unterminated"):
        scan("$(( 1 )")


def test_empty_var_body() -> None:
    # ${} is syntactically valid in the scanner; validation is the engine's job
    assert kinds("${}") == [(VAR, "")]


def test_empty_arith_body() -> None:
    # $(()) is syntactically valid in the scanner; validation is the engine's job
    assert kinds("$(())") == [(ARITH, "")]


def test_purely_literal_template() -> None:
    assert kinds("no dollars here") == [(LITERAL, "no dollars here")]


def test_empty_string_template() -> None:
    assert kinds("") == []
