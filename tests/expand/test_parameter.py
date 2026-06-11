from __future__ import annotations

from typing import Mapping

import pytest

from emergenv.expand.errors import ExpansionError
from emergenv.expand.parameter import expand_var

# ---------------------------------------------------------------------------
# Stub: the real recursion callback is engine.expand; for unit tests we use a
# faithful stub that handles:
#   - plain ${NAME}          -> look up in variables (error if unset)
#   - plain literals         -> returned as-is
# This is enough for most test cases.  Cases that need real nesting (e.g.
# ${A:-${B^^}}) are documented below; where the stub suffices they are tested
# here; where they need the full engine they live in test_expand.py (Task 6).
# ---------------------------------------------------------------------------


def fake_expand(template: str, variables: Mapping[str, str]) -> str:
    """Minimal expand callback: handles ${NAME} and bare literals only."""
    s = template
    # Handle a single bare ${NAME} with no operators
    if s.startswith("${") and s.endswith("}") and s.count("${") == 1:
        inner = s[2:-1]
        # Only handle simple names (no operators inside)
        if inner.isidentifier() or (
            inner.replace("_", "").isalnum() and inner[:1].isalpha()
        ):
            if inner not in variables:
                raise ExpansionError(f"undefined variable: {inner!r}")
            return variables[inner]
    # Return everything else literally (it is already plain text or unsupported
    # nested form; parameter.py only calls expand() on sub-templates that the
    # outer tests control).
    return template


def ev(body: str, **vars_: str) -> str:
    return expand_var(body, vars_, fake_expand)


# ===========================================================================
# Plain reference
# ===========================================================================


def test_plain_reference_set_nonempty() -> None:
    assert ev("VAR", VAR="x") == "x"


def test_plain_reference_set_empty() -> None:
    assert ev("VAR", VAR="") == ""


def test_plain_reference_unset_errors() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ev("VAR")


# ===========================================================================
# Defaults / required / alternate — full 6×3 colon matrix
# Form × State: {unset, empty, set-nonempty}
# ===========================================================================


class TestColonMinusColon:
    """${VAR:-word} — use word if unset OR empty."""

    def test_unset(self) -> None:
        assert ev("VAR:-d") == "d"

    def test_empty(self) -> None:
        assert ev("VAR:-d", VAR="") == "d"

    def test_set_nonempty(self) -> None:
        assert ev("VAR:-d", VAR="v") == "v"


class TestMinusNonColon:
    """${VAR-word} — use word only if unset."""

    def test_unset(self) -> None:
        assert ev("VAR-d") == "d"

    def test_empty(self) -> None:
        # empty does NOT trigger -
        assert ev("VAR-d", VAR="") == ""

    def test_set_nonempty(self) -> None:
        assert ev("VAR-d", VAR="v") == "v"


class TestColonQuestion:
    """${VAR:?word} — error if unset OR empty."""

    def test_unset(self) -> None:
        with pytest.raises(ExpansionError, match="boom"):
            ev("VAR:?boom")

    def test_empty(self) -> None:
        with pytest.raises(ExpansionError, match="boom"):
            ev("VAR:?boom", VAR="")

    def test_set_nonempty(self) -> None:
        assert ev("VAR:?boom", VAR="v") == "v"


class TestQuestionNonColon:
    """${VAR?word} — error only if unset."""

    def test_unset(self) -> None:
        with pytest.raises(ExpansionError, match="boom"):
            ev("VAR?boom")

    def test_empty(self) -> None:
        # empty does NOT trigger ?
        assert ev("VAR?boom", VAR="") == ""

    def test_set_nonempty(self) -> None:
        assert ev("VAR?boom", VAR="v") == "v"


class TestColonPlus:
    """${VAR:+word} — use word only if set AND non-empty."""

    def test_unset(self) -> None:
        assert ev("VAR:+set") == ""

    def test_empty(self) -> None:
        assert ev("VAR:+set", VAR="") == ""

    def test_set_nonempty(self) -> None:
        assert ev("VAR:+set", VAR="v") == "set"


class TestPlusNonColon:
    """${VAR+word} — use word if set (even empty)."""

    def test_unset(self) -> None:
        assert ev("VAR+set") == ""

    def test_empty(self) -> None:
        # plain + fires when set-but-empty
        assert ev("VAR+set", VAR="") == "set"

    def test_set_nonempty(self) -> None:
        assert ev("VAR+set", VAR="v") == "set"


# ===========================================================================
# :? edge cases
# ===========================================================================


def test_colon_question_no_message_uses_default() -> None:
    """${VAR:?} with no message should still error (default message)."""
    with pytest.raises(ExpansionError):
        ev("VAR:?")


def test_colon_question_custom_message_surfaced() -> None:
    """${VAR:?custom message} surfaces the message in the error."""
    with pytest.raises(ExpansionError, match="custom message"):
        ev("VAR:?custom message")


# ===========================================================================
# Default word is expanded
# ===========================================================================


def test_default_word_is_expanded() -> None:
    """${VAR:-${OTHER}} — the word is itself a parameter form."""
    assert ev("VAR:-${OTHER}", OTHER="o") == "o"


def test_alternate_word_is_expanded() -> None:
    assert ev("VAR:+${OTHER}", VAR="x", OTHER="o") == "o"


def test_default_word_plain_literal() -> None:
    assert ev("VAR:-hello") == "hello"


# ===========================================================================
# Length ${#VAR}
# ===========================================================================


def test_length_ascii() -> None:
    assert ev("#VAR", VAR="hello") == "5"


def test_length_empty() -> None:
    assert ev("#VAR", VAR="") == "0"


def test_length_unset_errors() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ev("#VAR")


def test_length_unicode_multibyte() -> None:
    # Each of these is exactly 1 code point, so len == number of chars
    assert ev("#VAR", VAR="café") == "4"  # 4 code points
    assert ev("#VAR", VAR="😀") == "1"  # single emoji = 1 code point
    assert ev("#VAR", VAR="日本語") == "3"  # 3 CJK chars


# ===========================================================================
# Substring
# ===========================================================================


def test_substring_positive_offset() -> None:
    assert ev("VAR:2", VAR="abcdef") == "cdef"


def test_substring_zero_offset() -> None:
    assert ev("VAR:0", VAR="abcdef") == "abcdef"


def test_substring_negative_offset_with_space() -> None:
    # leading space disambiguates from :-
    assert ev("VAR: -2", VAR="abcdef") == "ef"


def test_substring_negative_offset_3() -> None:
    assert ev("VAR: -3", VAR="abcdef") == "def"


def test_substring_offset_and_positive_length() -> None:
    assert ev("VAR:2:3", VAR="abcdef") == "cde"


def test_substring_negative_length() -> None:
    # length=-2 means "up to 2 chars before end"
    assert ev("VAR:0:-2", VAR="abcdef") == "abcd"


def test_substring_offset_past_end_empty() -> None:
    assert ev("VAR:10", VAR="abc") == ""


def test_substring_length_past_end_truncates() -> None:
    assert ev("VAR:1:999", VAR="abc") == "bc"


def test_substring_offset_negative_length_combo() -> None:
    # offset=1, length=-1 → value[1:-1]
    assert ev("VAR:1:-1", VAR="abcdef") == "bcde"


def test_substring_end_before_start_empty() -> None:
    # offset=4, length=-3 → start=4, end=max(4,6-3)=max(4,3)=4 → ""
    assert ev("VAR:4:-3", VAR="abcdef") == ""


def test_substring_ref_valued_offset() -> None:
    """offset via ${ref} that resolves to an integer."""
    # fake_expand handles simple ${NAME} references
    assert ev("VAR:${OFF}:2", VAR="abcdef", OFF="2") == "cd"


def test_substring_non_integer_offset_errors() -> None:
    with pytest.raises(ExpansionError, match="integer"):
        ev("VAR:abc", VAR="hello")


def test_substring_unset_errors() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ev("VAR:2")


# ===========================================================================
# Prefix / suffix removal
# ===========================================================================


def test_strip_prefix_shortest() -> None:
    assert ev("VAR#a*b", VAR="aabbcc") == "bcc"


def test_strip_prefix_longest() -> None:
    assert ev("VAR##a*b", VAR="aabbcc") == "cc"


def test_strip_suffix_shortest() -> None:
    assert ev("VAR%.*", VAR="a.b.c") == "a.b"


def test_strip_suffix_longest() -> None:
    assert ev("VAR%%.*", VAR="a.b.c") == "a"


def test_strip_prefix_question_mark() -> None:
    assert ev("VAR#?", VAR="hello") == "ello"


def test_strip_suffix_literal() -> None:
    # anchored literal strip with no wildcard
    assert ev("VAR%.gz", VAR="file.tar.gz") == "file.tar"


def test_strip_prefix_class() -> None:
    assert ev("VAR#[a-c]", VAR="abc") == "bc"


def test_strip_no_match_unchanged() -> None:
    assert ev("VAR#x*", VAR="hello") == "hello"


def test_strip_prefix_unset_errors() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ev("VAR#pat")


# ===========================================================================
# Search and replace
# ===========================================================================


def test_replace_first() -> None:
    assert ev("VAR/a/X", VAR="banana") == "bXnana"


def test_replace_all() -> None:
    assert ev("VAR//a/X", VAR="banana") == "bXnXnX"


def test_replace_anchor_start() -> None:
    assert ev("VAR/#ban/B", VAR="banana") == "Bana"


def test_replace_anchor_end() -> None:
    assert ev("VAR/%na/N", VAR="banana") == "banaN"


def test_replace_empty_replacement_deletes_first() -> None:
    assert ev("VAR/a", VAR="banana") == "bnana"


def test_replace_empty_replacement_deletes_all() -> None:
    assert ev("VAR//a", VAR="banana") == "bnn"


def test_replace_no_match_unchanged() -> None:
    assert ev("VAR/x/Y", VAR="banana") == "banana"


def test_replace_literal_slash_in_pattern() -> None:
    # Escaped slash in pattern: ${PATH//\//- } -> replace '/' with '-'
    assert ev("VAR//\\//X", VAR="a/b/c") == "aXbXc"


def test_replace_replacement_with_nested_var() -> None:
    """The replacement is a word template: nested ${REP} is expanded."""
    assert ev("VAR/a/${REP}", VAR="banana", REP="X") == "bXnana"


def test_replace_unset_errors() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ev("VAR/a/X")


# ===========================================================================
# SECURITY: substituted pattern is glob-escaped
# ===========================================================================


def test_replace_substituted_pattern_is_literal() -> None:
    # P's value contains '*'; it must match literally, not as a wildcard.
    assert ev("VAR//${P}/_", VAR="a*b*c", P="*") == "a_b_c"


def test_replace_substituted_pattern_question_literal() -> None:
    # P's value is '?'; must match literal '?' not any char.
    assert ev("VAR//${P}/X", VAR="a?b?c", P="?") == "aXbXc"


def test_strip_prefix_substituted_pattern_is_literal() -> None:
    # ${VAR#${P}} where P="*": literal '*' prefix, not a wildcard.
    # "a*bc" starts with literal '*'? No. So no match -> value unchanged.
    assert ev("VAR#${P}", VAR="a*bc", P="*") == "a*bc"


def test_strip_prefix_substituted_pattern_literal_match() -> None:
    # "${VAR#${P}}" where VAR="*bc" and P="*" -> strip literal '*' -> "bc"
    assert ev("VAR#${P}", VAR="*bc", P="*") == "bc"


# ===========================================================================
# Case modification
# ===========================================================================


def test_case_upper_first() -> None:
    assert ev("VAR^", VAR="hello") == "Hello"


def test_case_upper_all() -> None:
    assert ev("VAR^^", VAR="hello") == "HELLO"


def test_case_lower_first() -> None:
    assert ev("VAR,", VAR="HELLO") == "hELLO"


def test_case_lower_all() -> None:
    assert ev("VAR,,", VAR="HELLO") == "hello"


def test_case_already_correct() -> None:
    # Uppercasing already-uppercase: no change
    assert ev("VAR^^", VAR="HELLO") == "HELLO"
    assert ev("VAR,,", VAR="hello") == "hello"


def test_case_empty_string() -> None:
    assert ev("VAR^", VAR="") == ""
    assert ev("VAR^^", VAR="") == ""
    assert ev("VAR,", VAR="") == ""
    assert ev("VAR,,", VAR="") == ""


def test_case_unicode_upper_first() -> None:
    # 'ü' uppercases to 'Ü'
    assert ev("VAR^", VAR="über") == "Über"


def test_case_unicode_lower_all() -> None:
    assert ev("VAR,,", VAR="CAFÉ") == "café"


def test_case_unset_errors() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ev("VAR^")


# ===========================================================================
# Error cases: invalid names / unknown operators
# ===========================================================================


def test_invalid_name_leading_digit() -> None:
    with pytest.raises(ExpansionError):
        ev("1BAD")


def test_invalid_name_dash() -> None:
    with pytest.raises(ExpansionError):
        ev("-")


def test_invalid_name_empty() -> None:
    with pytest.raises(ExpansionError):
        ev("")


def test_unknown_operator() -> None:
    # An operator that isn't any of the known forms
    with pytest.raises(ExpansionError):
        ev("VAR@something", VAR="v")


# ===========================================================================
# M1: unsupported case-mod forms must error
# ===========================================================================


def test_case_mod_with_pattern_caret_errors() -> None:
    """${V^x} — pattern form is unsupported, must raise ExpansionError."""
    with pytest.raises(ExpansionError):
        ev("VAR^x", VAR="hello")


def test_case_mod_triple_caret_errors() -> None:
    """${V^^^} — more than two carets is unsupported."""
    with pytest.raises(ExpansionError):
        ev("VAR^^^", VAR="hello")


def test_case_mod_with_pattern_comma_errors() -> None:
    """${V,x} — pattern form is unsupported, must raise ExpansionError."""
    with pytest.raises(ExpansionError):
        ev("VAR,x", VAR="HELLO")


def test_case_mod_triple_comma_errors() -> None:
    """${V,,,} — more than two commas is unsupported."""
    with pytest.raises(ExpansionError):
        ev("VAR,,,", VAR="HELLO")


def test_case_valid_forms_still_work() -> None:
    """The four valid forms (^, ^^, ,, ,,) must remain functional."""
    assert ev("VAR^", VAR="hello") == "Hello"
    assert ev("VAR^^", VAR="hello") == "HELLO"
    assert ev("VAR,", VAR="HELLO") == "hELLO"
    assert ev("VAR,,", VAR="HELLO") == "hello"


# ===========================================================================
# M2: offset-via-ref and arithmetic-in-offset rejection
# ===========================================================================


def test_substring_offset_via_ref() -> None:
    """${V:${N}} with N="2" on "abcdef" -> "cdef"."""
    assert ev("VAR:${N}", VAR="abcdef", N="2") == "cdef"


def test_substring_negative_offset_via_ref() -> None:
    """${V: ${N}} with N="-2" on "abcdef" -> "ef" (leading space + ref)."""
    assert ev("VAR: ${N}", VAR="abcdef", N="-2") == "ef"


def test_substring_offset_and_length_via_refs() -> None:
    """${V:${N}:${L}} with N="1", L="3" on "abcdef" -> "bcd"."""
    assert ev("VAR:${N}:${L}", VAR="abcdef", N="1", L="3") == "bcd"


def test_substring_arithmetic_in_offset_errors() -> None:
    """${V:1+1} — arithmetic expressions in offset are not allowed."""
    with pytest.raises(ExpansionError, match="integer"):
        ev("VAR:1+1", VAR="abcdef")


def test_substring_non_integer_offset_errors_plain() -> None:
    """${V:x} — non-integer literal offset must raise ExpansionError."""
    with pytest.raises(ExpansionError, match="integer"):
        ev("VAR:x", VAR="abcdef")
