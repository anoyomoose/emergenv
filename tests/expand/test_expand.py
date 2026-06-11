"""End-to-end tests for the public expand() entry point.

All tests import only from the public surface ``emergenv.expand``.
These tests verify EXPANSION.md semantics end-to-end, including nesting,
safety properties, escaping, composition, and error propagation.
"""

from __future__ import annotations

import pytest

from emergenv.expand import ExpansionError, expand

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ex(template: str, **vars_: str) -> str:
    return expand(template, vars_)


# ---------------------------------------------------------------------------
# Basic / plan-specified tests
# ---------------------------------------------------------------------------


def test_literal_passthrough() -> None:
    assert ex("just text") == "just text"


def test_reference_and_composition() -> None:
    assert (
        ex("postgres://${U}@${H}/db", U="app", H="dbhost") == "postgres://app@dbhost/db"
    )


def test_unset_is_error_empty_is_ok() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ex("${MISSING}")
    assert ex("${E}x", E="") == "x"


def test_nested_default_uses_value_verbatim() -> None:
    # A unset -> use B's value; B's value contains ${C} which stays literal.
    assert ex("${A:-${B}}", B="${C}") == "${C}"


def test_arithmetic_inline() -> None:
    assert ex("port=$(( BASE + OFFSET ))", BASE="5000", OFFSET="80") == "port=5080"


def test_escaping_literal_dollar_and_brace() -> None:
    assert ex("$$") == "$"
    assert ex("$${B}") == "${B}"
    assert ex("cost is $5") == "cost is $5"


def test_combined_forms() -> None:
    assert ex("${URL%/}/api", URL="http://x/") == "http://x/api"
    assert ex("${NAME^^}", NAME="prod") == "PROD"
    assert ex("${PATH//\\//-}", PATH="a/b/c") == "a-b-c"


def test_no_reexpansion_of_substituted_value() -> None:
    # ${X} resolves to text containing ${Y}; it is NOT expanded further.
    assert ex("${X}", X="${Y}", Y="leak") == "${Y}"


# ---------------------------------------------------------------------------
# Real nesting that unit stubs couldn't fully exercise
# ---------------------------------------------------------------------------


def test_nested_default_with_case_modification() -> None:
    # ${A:-${B^^}} — A unset, use B uppercased
    assert ex("${A:-${B^^}}", B="hello") == "HELLO"


def test_triple_nested_default() -> None:
    # ${OUT:-${X:-${Y}}} — OUT and X unset, fall through to Y
    assert ex("${OUT:-${X:-${Y}}}", Y="deep") == "deep"
    # X set -> use X without reaching Y
    assert ex("${OUT:-${X:-${Y}}}", X="mid", Y="deep") == "mid"


def test_default_word_contains_arithmetic() -> None:
    # ${N:-$(( 1 + 1 ))} — N unset, word is an arithmetic block
    assert ex("${N:-$(( 1 + 1 ))}") == "2"
    # N set -> the arithmetic is never evaluated
    assert ex("${N:-$(( 1 + 1 ))}", N="given") == "given"


def test_pattern_with_nested_ref_stays_literal() -> None:
    # ${V//${P}/_} where P holds '*' — the substituted '*' must match literally
    # (confirms glob-escape is applied end-to-end through engine -> parameter)
    assert ex("${V//${P}/_}", V="a*b*c", P="*") == "a_b_c"
    # A literal * in the pattern (no variable substitution) acts as a wildcard:
    # it greedily matches "a*b*c" in one pass -> one replacement -> "_".
    # This confirms the two paths differ (substituted '*' matches only literal '*').
    assert ex("${V//*/_}", V="a*b*c") == "_"


# ---------------------------------------------------------------------------
# Safety properties end-to-end
# ---------------------------------------------------------------------------


def test_no_reexpansion_detail() -> None:
    # Substituted value contains expansion syntax — it stays literal
    assert ex("${X}", X="${Y}", Y="leak") == "${Y}"
    # Same via a default word: B's value is substituted, never re-scanned
    assert ex("${A:-${B}}", B="${C}") == "${C}"


def test_nested_default_inner_value_is_verbatim() -> None:
    # ${A:-${B}} with A unset; B's *value* (not template) is the result.
    # B's value happens to look like an expansion — it must not be expanded.
    assert ex("${A:-${B}}", B="${C}", C="SHOULD_NOT_APPEAR") == "${C}"


# ---------------------------------------------------------------------------
# Composition realism
# ---------------------------------------------------------------------------


def test_db_url_from_parts() -> None:
    result = ex(
        "postgres://${USER}:${PASS}@${HOST}:${PORT}/${DB}",
        USER="app",
        PASS="s3cr3t",
        HOST="db.internal",
        PORT="5432",
        DB="appdb",
    )
    assert result == "postgres://app:s3cr3t@db.internal:5432/appdb"


def test_port_from_arithmetic() -> None:
    # value built with arithmetic
    assert ex("$(( BASE + OFFSET ))", BASE="5000", OFFSET="32") == "5032"


def test_case_strip_replace_chained() -> None:
    # Multiple ${} in one template: uppercase, strip suffix, replace slashes
    result = ex(
        "${NAME^^}/${VER%.*}/${PATH//\\//-}",
        NAME="prod",
        VER="1.2.3",
        PATH="a/b/c",
    )
    assert result == "PROD/1.2/a-b-c"


# ---------------------------------------------------------------------------
# Escaping end-to-end
# ---------------------------------------------------------------------------


def test_double_dollar_is_literal_dollar() -> None:
    assert ex("$$") == "$"


def test_double_dollar_before_brace_is_literal() -> None:
    # $${B} → "${B}" (a literal dollar sign followed by the literal text {B})
    assert ex("$${B}") == "${B}"


def test_lone_dollar_in_value() -> None:
    # Ordinary $ not before { or (( is literal text
    assert ex("cost $5") == "cost $5"
    assert ex("100%") == "100%"


def test_literal_arith_via_double_dollar() -> None:
    # $$((  → "$((", i.e. $$  then literal "((" — the arithmetic is NOT started
    assert ex("$$(( 1 + 1 ))") == "$(( 1 + 1 ))"


# ---------------------------------------------------------------------------
# Errors propagate as ExpansionError from each layer through expand()
# ---------------------------------------------------------------------------


def test_error_undefined_variable() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ex("${NOPE}")


def test_error_bad_arithmetic() -> None:
    with pytest.raises(ExpansionError):
        ex("$(( 1 / 0 ))")


def test_error_required_form() -> None:
    with pytest.raises(ExpansionError, match="must be set"):
        ex("${VAR:?must be set}")


def test_error_unterminated_brace() -> None:
    with pytest.raises(ExpansionError, match="unterminated"):
        ex("${B")


def test_error_dollar_inside_arith() -> None:
    # $( ($ X)) — $ inside $((…)) is illegal
    with pytest.raises(ExpansionError, match=r"\$"):
        ex("$(( ${X} + 1 ))", X="5")


def test_error_bad_arith_non_integer() -> None:
    with pytest.raises(ExpansionError, match="integer"):
        ex("$(( A + 1 ))", A="abc")


# ---------------------------------------------------------------------------
# __all__ and import surface
# ---------------------------------------------------------------------------


def test_all_exports_exactly_expand_and_error() -> None:
    import emergenv.expand as pkg

    assert set(pkg.__all__) == {"expand", "ExpansionError"}


def test_public_import_works() -> None:
    # The import at the top of this file already tests this; exercise it again
    # explicitly so the test name documents the intent.
    from emergenv.expand import expand as _expand  # noqa: F401 (used implicitly)

    assert callable(_expand)
