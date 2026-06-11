"""Tests for emergenv.expand.glob — plan tests + extra edge cases."""

from __future__ import annotations

import pytest

from emergenv.expand.errors import ExpansionError
from emergenv.expand.glob import escape, remove_prefix, remove_suffix, replace

# ---------------------------------------------------------------------------
# Plan tests (from Task 2, Step 1)
# ---------------------------------------------------------------------------


def test_remove_prefix_shortest_and_longest() -> None:
    assert remove_prefix("aabbcc", "a*b", longest=False) == "bcc"  # shortest "aab"
    assert remove_prefix("aabbcc", "a*b", longest=True) == "cc"  # longest "aabb"
    assert remove_prefix("nochange", "x*", longest=True) == "nochange"


def test_remove_suffix_shortest_and_longest() -> None:
    # Pattern ".*" (dot then anything): bash ${f%.*} idiom.
    # Shortest suffix matching ".*" in "file.tar.gz" is ".gz" → leaves "file.tar".
    # Longest suffix matching ".*" is ".tar.gz" → leaves "file".
    assert remove_suffix("file.tar.gz", ".*", longest=False) == "file.tar"
    assert remove_suffix("file.tar.gz", ".*", longest=True) == "file"
    assert remove_suffix("path/to", ".x", longest=True) == "path/to"


def test_remove_question_and_class() -> None:
    assert remove_prefix("abc", "?", longest=False) == "bc"
    assert remove_prefix("abc", "[a-c]", longest=False) == "bc"
    assert remove_prefix("xbc", "[!a-c]", longest=False) == "bc"


def test_replace_first_all_and_anchors() -> None:
    assert replace("a/b/c", "/", "-", all_=False, anchor=None) == "a-b/c"
    assert replace("a/b/c", "/", "-", all_=True, anchor=None) == "a-b-c"
    assert (
        replace("http://x", "http", "https", all_=False, anchor="start") == "https://x"
    )
    assert replace("x.txt", ".txt", ".md", all_=False, anchor="end") == "x.md"
    assert (
        replace("x.txt", ".txt", ".md", all_=False, anchor="start") == "x.txt"
    )  # no start match


def test_replace_with_empty_replacement_deletes() -> None:
    assert replace("a b c", " ", "", all_=True, anchor=None) == "abc"


def test_backslash_escapes_metacharacter() -> None:
    # literal star, not a wildcard
    assert remove_prefix("*abc", r"\*", longest=False) == "abc"
    assert (
        remove_prefix("xabc", r"\*", longest=False) == "xabc"
    )  # no literal star -> no match


def test_escape_neutralises_metacharacters() -> None:
    assert (
        remove_prefix("a*b", escape("a*b"), longest=False) == ""
    )  # whole thing matched literally
    assert (
        remove_prefix("aXb", escape("a*b"), longest=False) == "aXb"
    )  # '*' is literal, no match


def test_unterminated_class_errors() -> None:
    with pytest.raises(ExpansionError, match="unterminated"):
        remove_prefix("abc", "[a-c", longest=False)


def test_dangling_backslash_errors() -> None:
    with pytest.raises(ExpansionError, match="backslash"):
        remove_prefix("abc", "a\\", longest=False)


# ---------------------------------------------------------------------------
# Extra edge-case tests (mandate)
# ---------------------------------------------------------------------------


def test_star_matches_empty_string() -> None:
    # '*' should match an empty sequence of chars
    assert (
        remove_prefix("abc", "*", longest=False) == "abc"
    )  # shortest: match empty at start
    assert (
        remove_prefix("abc", "*", longest=True) == ""
    )  # longest: consume whole string
    assert (
        remove_suffix("abc", "*", longest=False) == "abc"
    )  # shortest: match empty at end
    assert (
        remove_suffix("abc", "*", longest=True) == ""
    )  # longest: consume whole string


def test_question_requires_exactly_one_char() -> None:
    # '?' must match exactly one char — not zero, not two
    assert remove_prefix("a", "?", longest=False) == ""  # exactly one char consumed
    assert (
        remove_prefix("", "?", longest=False) == ""
    )  # empty string: no match, unchanged
    # Two ?'s match two chars
    assert remove_prefix("ab", "??", longest=False) == ""
    assert remove_prefix("a", "??", longest=False) == "a"  # only one char, no match


def test_nested_and_adjacent_metacharacters() -> None:
    # "**" behaves like a single "*" (both are ".*" in regex, so ".*.*" matches same)
    assert remove_prefix("anything", "**", longest=True) == ""
    # "?*" — one char then any (including empty) remainder
    assert remove_prefix("xhello", "?*", longest=True) == ""
    assert remove_prefix("xhello", "?*", longest=False) == "hello"
    # "*?" — any prefix then one required char
    assert (
        remove_prefix("ab", "*?", longest=False) == "b"
    )  # shortest: * matches empty, ? matches 'a'


def test_ranges_combined_with_literals() -> None:
    # literal 'p' + range [0-9] + literal 'x'
    assert remove_prefix("p5x-rest", "p[0-9]x", longest=False) == "-rest"
    assert (
        remove_prefix("pAxrest", "p[0-9]x", longest=False) == "pAxrest"
    )  # no digit → no match
    # range at end
    assert remove_suffix("hello3", "o[0-9]", longest=False) == "hell"
    assert remove_suffix("helloA", "o[0-9]", longest=False) == "helloA"  # no match


def test_negated_class_bang() -> None:
    # [!abc] matches any char NOT in {a,b,c}
    assert remove_prefix("xbc", "[!abc]", longest=False) == "bc"
    assert (
        remove_prefix("abc", "[!abc]", longest=False) == "abc"
    )  # 'a' IS in set → no match


def test_negated_class_caret() -> None:
    # [^abc] is equivalent to [!abc]
    assert remove_prefix("xbc", "[^abc]", longest=False) == "bc"
    assert remove_prefix("abc", "[^abc]", longest=False) == "abc"


def test_close_bracket_as_first_member_of_class() -> None:
    # A ']' immediately after '[' (or '[!') is treated as a literal member.
    # Pattern "[]]" should match a literal ']'
    assert remove_prefix("]rest", "[]]", longest=False) == "rest"
    assert remove_prefix("xrest", "[]]", longest=False) == "xrest"  # 'x' not ']'
    # "[!]]" should match any char that is NOT ']'
    assert remove_prefix("xrest", "[!]]", longest=False) == "rest"
    assert remove_prefix("]rest", "[!]]", longest=False) == "]rest"  # ']' is excluded


def test_replace_anchored_end_no_match() -> None:
    # anchor="end" when pattern doesn't match at end → value unchanged
    assert replace("hello.py", ".txt", ".md", all_=False, anchor="end") == "hello.py"


def test_replace_anchored_start_no_match() -> None:
    # anchor="start" when pattern doesn't match at start → value unchanged
    assert (
        replace("world_hello", "hello", "HI", all_=False, anchor="start")
        == "world_hello"
    )


def test_remove_on_empty_string() -> None:
    # Removing from an empty string always returns empty
    assert remove_prefix("", "a*", longest=False) == ""
    assert remove_prefix("", "a*", longest=True) == ""
    assert remove_suffix("", "*z", longest=False) == ""
    assert remove_suffix("", "*z", longest=True) == ""
    # A '*' pattern matches empty string itself
    assert remove_prefix("", "*", longest=False) == ""
    assert remove_prefix("", "*", longest=True) == ""


def test_replace_overlapping_ish_patterns() -> None:
    # Greedy, non-overlapping replacement of "aa" in "aaaa"
    assert replace("aaaa", "aa", "X", all_=True, anchor=None) == "XX"
    # Replace first only
    assert replace("aaaa", "aa", "X", all_=False, anchor=None) == "Xaa"


def test_escape_question_and_bracket() -> None:
    # escape() covers '?' and '['
    assert (
        remove_prefix("?bc", escape("?bc"), longest=False) == ""
    )  # literal '?' matched
    assert remove_prefix("abc", escape("?bc"), longest=False) == "abc"  # 'a' ≠ '?'
    assert (
        remove_prefix("[bc", escape("[bc"), longest=False) == ""
    )  # literal '[' matched
    assert remove_prefix("abc", escape("[bc"), longest=False) == "abc"  # 'a' ≠ '['


def test_star_replace_with_anchor() -> None:
    # anchor="start", pattern="*" matches the whole string from the start
    assert replace("hello", "*", "X", all_=False, anchor="start") == "X"
    # anchor="end", pattern="*" matches the whole string from the end
    assert replace("hello", "*", "X", all_=False, anchor="end") == "X"


def test_remove_suffix_question_mark() -> None:
    # '?' at the end of a suffix pattern
    assert remove_suffix("abcx", "c?", longest=False) == "ab"
    assert remove_suffix("abcx", "c?", longest=True) == "ab"
    assert remove_suffix("ab", "c?", longest=False) == "ab"  # no match


def test_mixed_literal_and_wildcard_prefix() -> None:
    # Ensure literals and wildcards combine correctly.
    # "hello ?" matches "hello " + exactly one char = "hello w" (7 chars), leaving "orld".
    assert remove_prefix("hello world", "hello ?", longest=False) == "orld"
    # "hello *" can match "hello " (6 chars, * matches empty) which is the shortest.
    assert remove_prefix("hello world", "hello *", longest=False) == "world"
    assert remove_prefix("hello world", "hello *", longest=True) == ""


# ---------------------------------------------------------------------------
# C1 regression: zero-length match adjacent to consumed match (greedy * bug)
# ---------------------------------------------------------------------------


def test_replace_star_all_no_spurious_extra_match() -> None:
    # ${V//*/_} on "a*b*c" -> bash gives "_" (one greedy match, not two)
    assert replace("a*b*c", "*", "_", all_=True, anchor=None) == "_"


def test_replace_star_all_shorter_string() -> None:
    # "a*b" greedy * matches whole string in one pass -> "_"
    assert replace("a*b", "*", "_", all_=True, anchor=None) == "_"


def test_replace_star_all_empty_string() -> None:
    # empty string: * matches empty -> "_"
    assert replace("", "*", "_", all_=True, anchor=None) == "_"


def test_replace_star_first_matches_whole() -> None:
    # ${V/*/_} on "abc" -> one replacement of the whole string
    assert replace("abc", "*", "_", all_=False, anchor=None) == "_"


def test_replace_question_all() -> None:
    # ${V//?/x} on "abc" -> each char replaced -> "xxx"
    assert replace("abc", "?", "x", all_=True, anchor=None) == "xxx"


def test_replace_no_match_unchanged() -> None:
    # ${V//x/y} on "abc" -> no match -> "abc"
    assert replace("abc", "x", "y", all_=True, anchor=None) == "abc"


def test_replace_star_all_with_literal_star() -> None:
    # ${V//a*/_} on "a*b*c" -> "a*" matches from start greedy -> "_"
    assert replace("a*b*c", "a*", "_", all_=True, anchor=None) == "_"


def test_replace_anchor_start_star() -> None:
    # ${V/#*/x} on "abc" -> matches the whole string at start -> "x"
    assert replace("abc", "*", "x", all_=False, anchor="start") == "x"


def test_replace_anchor_end_star() -> None:
    # ${V/%*/x} on "abc" -> matches the whole string at end -> "x"
    assert replace("abc", "*", "x", all_=False, anchor="end") == "x"
