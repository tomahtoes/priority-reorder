import math

import pytest

from utils import KANJI_RE, is_kanji, parse_sort_value, to_hiragana, parse_comparator


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


def test_to_hiragana_matches_reference_char_loop():
    # Pin the translate-table implementation against the original per-char loop
    # over the whole kana neighborhood plus ASCII and an astral kanji.
    def reference(text):
        return "".join(
            chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c
            for c in text
        )

    probe = "".join(chr(cp) for cp in range(0x3000, 0x3110)) + "abc123 " + "\U00020B9F"
    assert to_hiragana(probe) == reference(probe)


def test_to_hiragana_range_boundaries():
    assert to_hiragana("゠") == "゠"  # ゠ just below the range: unchanged
    assert to_hiragana("ァ") == "ぁ"      # first folded code point
    assert to_hiragana("ヶ") == "ゖ"      # last folded code point (ヶ)
    assert to_hiragana("ヷ") == "ヷ"  # ヷ just above the range: unchanged
    assert to_hiragana("ー") == "ー"           # U+30FC length mark: unchanged


# --- is_kanji ----------------------------------------------------------------

def test_is_kanji_true_for_ideographs():
    assert is_kanji("手")
    assert is_kanji("漢")
    assert is_kanji("𠮟")  # U+20B9F, supplementary plane (Ext B)


def test_is_kanji_false_for_kana_ascii_symbols():
    for ch in ("て", "ヲ", "a", "1", "㋕", "々"):
        assert not is_kanji(ch), ch


# --- KANJI_RE ---------------------------------------------------------------

def test_kanji_re_agrees_with_is_kanji():
    # The two must stay interchangeable: kanji_manager scans whole strings with the regex
    # while dictionary_manager._kanji_skeleton tests characters with is_kanji, and a
    # narrower regex silently made kanji:num/kanji:new blind to Ext A / compatibility /
    # Ext B characters the skeleton counted.
    probes = list(range(0x2E00, 0x10000)) + list(range(0x10000, 0x40000, 13))
    disagreements = [
        hex(cp) for cp in probes
        if bool(KANJI_RE.match(chr(cp))) != is_kanji(chr(cp))
    ]
    assert disagreements == []


def test_kanji_re_finds_supplementary_and_compatibility_ideographs():
    assert KANJI_RE.findall("𠮟る") == ["𠮟"]   # U+20B9F, Ext B
    assert KANJI_RE.findall("﨑") == ["﨑"]      # U+FA11, compatibility
    assert KANJI_RE.findall("彫刻abcの12") == ["彫", "刻"]
    assert KANJI_RE.findall("ひらがなカナ") == []


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
