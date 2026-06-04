import math

import pytest

from utils import parse_sort_value, to_hiragana, parse_comparator


# --- parse_sort_value -------------------------------------------------------

def test_parse_sort_value_valid_positive():
    assert parse_sort_value("123") == (123.0, True)
    assert parse_sort_value("4.5") == (4.5, True)


def test_parse_sort_value_empty_is_missing():
    val, has = parse_sort_value("")
    assert has is False and math.isinf(val)


def test_parse_sort_value_non_numeric_is_missing():
    val, has = parse_sort_value("abc")
    assert has is False and math.isinf(val)


def test_parse_sort_value_zero_and_negative_are_missing():
    # <= 0 has no usable ordering data (cards must trail), so has_value is False.
    for s in ("0", "-3"):
        val, has = parse_sort_value(s)
        assert has is False and math.isinf(val)


# --- to_hiragana ------------------------------------------------------------

def test_to_hiragana_folds_katakana():
    assert to_hiragana("ギリギリ") == "ぎりぎり"
    assert to_hiragana("カタカナ") == "かたかな"


def test_to_hiragana_leaves_hiragana_kanji_ascii_untouched():
    assert to_hiragana("ひらがな") == "ひらがな"
    assert to_hiragana("漢字abc1") == "漢字abc1"


def test_to_hiragana_mixed_string():
    assert to_hiragana("お茶ハ") == "お茶は"  # only the katacana ハ folds to は


# --- parse_comparator -------------------------------------------------------

@pytest.mark.parametrize("op,a,b,expected", [
    ("=", 3, 3, True), ("=", 3, 4, False),
    ("!=", 3, 4, True), ("!=", 3, 3, False),
    ("<", 2, 3, True), ("<", 3, 2, False),
    ("<=", 3, 3, True), ("<=", 4, 3, False),
    (">", 3, 2, True), (">", 2, 3, False),
    (">=", 3, 3, True), (">=", 2, 3, False),
])
def test_parse_comparator_operators(op, a, b, expected):
    assert parse_comparator(op)(a, b) is expected


def test_parse_comparator_unknown_raises():
    with pytest.raises(ValueError):
        parse_comparator("~")
