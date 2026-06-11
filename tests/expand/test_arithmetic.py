from __future__ import annotations

import pytest

from emergenv.expand.arithmetic import evaluate
from emergenv.expand.errors import ExpansionError


def ev(expr: str, **vars_: str) -> int:
    return evaluate(expr, vars_)


# ---------------------------------------------------------------------------
# Plan tests
# ---------------------------------------------------------------------------


def test_basic_arithmetic_and_precedence() -> None:
    assert ev("1 + 2 * 3") == 7
    assert ev("(1 + 2) * 3") == 9
    assert ev("2 ** 3 ** 2") == 512  # right-associative
    assert ev("-2 ** 2") == -4  # ** binds tighter than unary minus
    assert ev("7 % 3") == 1


def test_truncating_division_and_modulo() -> None:
    assert ev("-7 / 2") == -3  # toward zero, not floor (-4)
    assert ev("-7 % 2") == -1
    assert ev("7 / -2") == -3


def test_names_resolve_from_variables() -> None:
    assert ev("BASE + OFFSET", BASE="5000", OFFSET="80") == 5080


def test_integer_literal_bases() -> None:
    assert ev("0x1F") == 31
    assert ev("0o17") == 15
    assert ev("0b101") == 5


def test_ambiguous_leading_zero_is_error() -> None:
    with pytest.raises(ExpansionError, match="ambiguous|invalid"):
        ev("010")
    with pytest.raises(ExpansionError, match="ambiguous|invalid"):
        ev("N", N="010")


def test_bitwise_comparison_logical_ternary() -> None:
    assert ev("6 & 3") == 2
    assert ev("6 | 1") == 7
    assert ev("5 ^ 1") == 4
    assert ev("1 << 4") == 16
    assert ev("~0") == -1
    assert ev("3 > 2") == 1
    assert ev("3 == 2") == 0
    assert ev("!0") == 1
    assert ev("0 && 1") == 0
    assert ev("0 || 5") == 1
    assert ev("1 ? 7 : 9") == 7
    assert ev("0 ? 7 : 9") == 9


def test_dollar_is_illegal() -> None:
    with pytest.raises(ExpansionError, match=r"\$"):
        ev("${X} + 1")


def test_undefined_name_is_error() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ev("MISSING + 1")


def test_non_integer_value_is_error() -> None:
    with pytest.raises(ExpansionError, match="integer"):
        ev("X + 1", X="abc")
    with pytest.raises(ExpansionError, match="integer"):
        ev("X + 1", X="1.5")


def test_division_by_zero_and_negative_exponent() -> None:
    with pytest.raises(ExpansionError, match="zero"):
        ev("1 / 0")
    with pytest.raises(ExpansionError, match="zero"):
        ev("1 % 0")
    with pytest.raises(ExpansionError, match="exponent"):
        ev("2 ** -1")


def test_unexpected_token_errors() -> None:
    with pytest.raises(ExpansionError):
        ev("1 +")
    with pytest.raises(ExpansionError):
        ev("1 2")


# ---------------------------------------------------------------------------
# Additional thoroughness tests
# ---------------------------------------------------------------------------


# --- Precedence between levels ---


def test_precedence_arithmetic_interaction() -> None:
    # 1 + 2*3 - 4/2 => 1 + 6 - 2 => 5
    assert ev("1 + 2 * 3 - 4 / 2") == 5
    # mixed shifts and bitwise: 1 | 2 & 3 => 1 | 2 => 3  (& binds tighter)
    assert ev("1 | 2 & 3") == 3
    # comparison: 2+3 < 4+1 => 5 < 5 => 0
    assert ev("2 + 3 < 4 + 1") == 0
    # comparison: 2+3 <= 4+1 => 5 <= 5 => 1
    assert ev("2 + 3 <= 4 + 1") == 1


def test_precedence_shift_vs_arithmetic() -> None:
    # 2 + 1 << 2 => (2+1)<<2 = 3<<2 = 12, since << binds looser than +
    assert ev("2 + 1 << 2") == 12
    # 1 << 2 + 1 => 1<<(2+1) = 1<<3 = 8, since + is tighter
    assert ev("1 << 2 + 1") == 8


def test_precedence_bitwise_levels() -> None:
    # & > ^ > |
    # 1 | 2 ^ 3 & 7 => 1 | 2 ^ (3&7) => 1 | 2^3 => 1 | 1 => 1
    assert ev("1 | 2 ^ 3 & 7") == 1
    # 7 & 5 | 2 => (7&5)|2 = 5|2 = 7
    assert ev("7 & 5 | 2") == 7


def test_precedence_logical_vs_comparison() -> None:
    # 3 > 2 && 1 < 5 => 1 && 1 => 1
    assert ev("3 > 2 && 1 < 5") == 1
    # 0 || 3 > 2 => 0 || 1 => 1
    assert ev("0 || 3 > 2") == 1


def test_precedence_eq_neq_vs_comparison() -> None:
    # (3 < 5) == 1 => 1 == 1 => 1
    assert ev("3 < 5 == 1") == 1


def test_precedence_ternary_lowest() -> None:
    # ternary is lowest: 1+1 ? 2*3 : 4+4 => 2 ? 6 : 8 => 6
    assert ev("1 + 1 ? 2 * 3 : 4 + 4") == 6


def test_precedence_mixing_all() -> None:
    # 2 | 1 & 3 ^ 1 => 2 | (1 & 3) ^ 1 => 2 | 1 ^ 1 => 2 | 0 => 2
    # & binds tightest of bitwise, then ^, then |
    assert ev("2 | 1 & 3 ^ 1") == 2


# --- Associativity ---


def test_left_assoc_subtraction() -> None:
    # 10 - 3 - 2 == (10-3)-2 == 5, not 10-(3-2)=9
    assert ev("10 - 3 - 2") == 5


def test_left_assoc_division() -> None:
    # 100 / 5 / 2 == (100/5)/2 == 10, not 100/(5/2)=40
    assert ev("100 / 5 / 2") == 10


def test_left_assoc_modulo() -> None:
    # 17 % 5 % 3 == (17%5)%3 == 2%3 == 2
    assert ev("17 % 5 % 3") == 2


def test_right_assoc_power() -> None:
    # 2 ** 3 ** 2 == 2 ** (3**2) == 2**9 == 512
    assert ev("2 ** 3 ** 2") == 512


def test_right_assoc_ternary() -> None:
    # 1 ? 2 : 0 ? 3 : 4 => since ternary is right-assoc: 1 ? 2 : (0 ? 3 : 4) => 2
    assert ev("1 ? 2 : 0 ? 3 : 4") == 2
    # 0 ? 2 : 0 ? 3 : 4 => 0 ? 2 : (0 ? 3 : 4) => 0 ? 3 : 4 => 4
    assert ev("0 ? 2 : 0 ? 3 : 4") == 4


# --- Unary operators ---


def test_unary_double_negative() -> None:
    # --5 should be -(-5) = 5
    assert ev("--5") == 5


def test_unary_double_negative_explicit_parens() -> None:
    assert ev("-(-5)") == 5


def test_unary_double_not() -> None:
    # !!5 == !(!5) == !(0) == 1
    assert ev("!!5") == 1
    assert ev("!!0") == 0


def test_unary_double_bitwise_not() -> None:
    # ~~5 == ~(~5) == ~(-6) == 5
    assert ev("~~5") == 5


def test_unary_minus_with_power() -> None:
    # -2 ** 2 => -(2**2) = -4 (** binds tighter than unary -)
    assert ev("-2 ** 2") == -4


def test_power_with_unary_in_exponent_errors() -> None:
    # 2 ** -1 is an error (negative exponent)
    with pytest.raises(ExpansionError, match="exponent"):
        ev("2 ** -1")


def test_unary_plus() -> None:
    assert ev("+5") == 5
    assert ev("+-5") == -5


# --- Truncating division / modulo sign matrix ---


def test_trunc_div_all_sign_combinations() -> None:
    # truncate toward zero
    assert ev("7 / 2") == 3  # pos / pos -> floor same as trunc
    assert ev("-7 / 2") == -3  # neg / pos -> -3 (trunc), not -4 (floor)
    assert ev("7 / -2") == -3  # pos / neg -> -3
    assert ev("-7 / -2") == 3  # neg / neg -> 3


def test_trunc_mod_all_sign_combinations() -> None:
    # a % b == a - (a/b)*b, with trunc div
    assert ev("7 % 2") == 1  # 7 - 3*2 = 1
    assert ev("-7 % 2") == -1  # -7 - (-3)*2 = -7+6 = -1
    assert ev("7 % -2") == 1  # 7 - (-3)*(-2) = 7-6 = 1
    assert ev("-7 % -2") == -1  # -7 - 3*(-2) = -7+6 = -1


# --- Literals ---


def test_decimal_literal() -> None:
    assert ev("42") == 42
    assert ev("0") == 0


def test_hex_literals_upper_and_lower() -> None:
    assert ev("0x1F") == 31
    assert ev("0X1f") == 31
    assert ev("0xFF") == 255
    assert ev("0xff") == 255


def test_octal_literals_upper_and_lower() -> None:
    assert ev("0o17") == 15
    assert ev("0O17") == 15
    assert ev("0o10") == 8


def test_binary_literals_upper_and_lower() -> None:
    assert ev("0b101") == 5
    assert ev("0B1010") == 10


def test_bare_zero_is_ok() -> None:
    assert ev("0") == 0


def test_octal_zero_prefix_error() -> None:
    # 010 is ambiguous
    with pytest.raises(ExpansionError, match="ambiguous|invalid"):
        ev("010")


def test_invalid_digit_error() -> None:
    # 08 has invalid octal digit, but actually should be caught
    # as ambiguous leading zero (it starts with 0 and is not 0x/0o/0b)
    with pytest.raises(ExpansionError, match="ambiguous|invalid"):
        ev("08")


def test_variable_value_with_leading_zero_error() -> None:
    with pytest.raises(ExpansionError, match="ambiguous|invalid"):
        ev("N", N="010")


# --- Names / variables ---


def test_name_from_variables() -> None:
    assert ev("A", A="42") == 42
    assert ev("A + B", A="10", B="20") == 30


def test_undefined_variable_error() -> None:
    with pytest.raises(ExpansionError, match="undefined"):
        ev("MISSING")


def test_non_integer_variable_abc_error() -> None:
    with pytest.raises(ExpansionError, match="integer"):
        ev("X", X="abc")


def test_non_integer_variable_float_error() -> None:
    with pytest.raises(ExpansionError, match="integer"):
        ev("X", X="1.5")


def test_empty_variable_value_error() -> None:
    with pytest.raises(ExpansionError, match="integer"):
        ev("X", X="")


def test_variable_with_surrounding_whitespace() -> None:
    # a variable value of "  42  " should still parse as 42
    assert ev("X", X="  42  ") == 42


def test_variable_with_hex_value() -> None:
    assert ev("X", X="0xFF") == 255


# --- Errors ---


def test_dollar_anywhere_illegal() -> None:
    with pytest.raises(ExpansionError, match=r"\$"):
        ev("$X + 1")
    with pytest.raises(ExpansionError, match=r"\$"):
        ev("1 + $X")
    with pytest.raises(ExpansionError, match=r"\$"):
        ev("${X}")


def test_division_by_zero() -> None:
    with pytest.raises(ExpansionError, match="zero"):
        ev("1 / 0")


def test_modulo_by_zero() -> None:
    with pytest.raises(ExpansionError, match="zero"):
        ev("5 % 0")


def test_trailing_operator_error() -> None:
    with pytest.raises(ExpansionError):
        ev("1 +")
    with pytest.raises(ExpansionError):
        ev("1 *")


def test_two_adjacent_numbers_error() -> None:
    with pytest.raises(ExpansionError):
        ev("1 2")


def test_unbalanced_open_paren_error() -> None:
    with pytest.raises(ExpansionError):
        ev("(1 + 2")


def test_unbalanced_close_paren_error() -> None:
    with pytest.raises(ExpansionError):
        ev("1 + 2)")


def test_empty_expression_error() -> None:
    with pytest.raises(ExpansionError):
        ev("")


def test_unknown_character_error() -> None:
    with pytest.raises(ExpansionError):
        ev("1 @ 2")


def test_ternary_missing_colon_error() -> None:
    with pytest.raises(ExpansionError):
        ev("1 ? 2")


# --- Whitespace variants ---


def test_no_spaces() -> None:
    assert ev("1+2*3") == 7


def test_tabs_as_whitespace() -> None:
    assert ev("1\t+\t2") == 3


def test_newlines_as_whitespace() -> None:
    assert ev("1\n+\n2") == 3


def test_extra_spaces_ignored() -> None:
    assert ev("  1  +  2  ") == 3


# --- Other interesting cases ---


def test_zero_exponent() -> None:
    # anything ** 0 == 1
    assert ev("5 ** 0") == 1
    assert ev("0 ** 0") == 1


def test_nested_parens() -> None:
    assert ev("((2 + 3))") == 5
    assert ev("((2 + 3) * (4 - 1))") == 15


def test_ternary_nested() -> None:
    # nested ternary in then-branch
    assert ev("1 ? (1 ? 99 : 0) : 0") == 99
    # nested ternary in else-branch
    assert ev("0 ? 0 : (1 ? 99 : 0)") == 99


def test_large_number() -> None:
    assert ev("2 ** 62") == 4611686018427387904


def test_bitwise_not_large() -> None:
    assert ev("~(-1)") == 0


def test_shift_left_and_right() -> None:
    assert ev("1 << 10") == 1024
    assert ev("1024 >> 10") == 1
    assert ev("16 >> 2") == 4


def test_logical_short_circuit_values() -> None:
    # && returns 1/0
    assert ev("5 && 3") == 1
    assert ev("0 && 3") == 0
    # || returns 1/0
    assert ev("5 || 0") == 1
    assert ev("0 || 0") == 0


def test_comparison_operators() -> None:
    assert ev("5 == 5") == 1
    assert ev("5 == 4") == 0
    assert ev("5 != 4") == 1
    assert ev("5 != 5") == 0
    assert ev("5 > 4") == 1
    assert ev("4 > 5") == 0
    assert ev("5 >= 5") == 1
    assert ev("5 >= 6") == 0
    assert ev("4 < 5") == 1
    assert ev("5 < 4") == 0
    assert ev("5 <= 5") == 1
    assert ev("6 <= 5") == 0


# ---------------------------------------------------------------------------
# Magnitude / DoS guard tests (CRITICAL fix #1)
# ---------------------------------------------------------------------------


def test_huge_left_shift_raises() -> None:
    with pytest.raises(ExpansionError):
        ev("1 << 100000000000")


def test_huge_power_raises() -> None:
    with pytest.raises(ExpansionError):
        ev("10 ** 1000000")


def test_negative_left_shift_raises() -> None:
    with pytest.raises(ExpansionError):
        ev("1 << -1")


def test_negative_right_shift_raises() -> None:
    with pytest.raises(ExpansionError):
        ev("1 >> -1")


def test_large_but_bounded_shift_works() -> None:
    # 1 << 64 is within the 4096-bit cap and should succeed
    assert ev("1 << 64") == 2**64


def test_large_but_bounded_power_works() -> None:
    assert ev("2 ** 64") == 2**64


# ---------------------------------------------------------------------------
# Variable-value literal rules (IMPORTANT fix #2 + #3)
# ---------------------------------------------------------------------------


def test_variable_underscore_rejected() -> None:
    with pytest.raises(ExpansionError, match="integer"):
        ev("X", X="1_000")


def test_variable_leading_minus_works() -> None:
    assert ev("X", X="-5") == -5


def test_variable_leading_plus_works() -> None:
    assert ev("X", X="+5") == 5


def test_variable_leading_zero_rejected() -> None:
    with pytest.raises(ExpansionError, match="ambiguous|invalid"):
        ev("X", X="010")


def test_variable_hex_value_works() -> None:
    assert ev("X", X="0x1f") == 31


def test_variable_empty_names_var_in_message() -> None:
    with pytest.raises(ExpansionError, match="integer") as exc_info:
        ev("X", X="")
    assert "X" in str(exc_info.value)


def test_variable_abc_names_var_in_message() -> None:
    with pytest.raises(ExpansionError, match="integer") as exc_info:
        ev("X", X="abc")
    assert "X" in str(exc_info.value)


def test_variable_float_names_var_in_message() -> None:
    with pytest.raises(ExpansionError, match="integer") as exc_info:
        ev("X", X="1.5")
    assert "X" in str(exc_info.value)
