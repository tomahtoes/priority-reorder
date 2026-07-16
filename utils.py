from typing import Callable, Any, Tuple

def parse_sort_value(sort_val_str: str) -> Tuple[float, bool]:
    """Returns (sort_value, has_value). Empty / non-numeric / <= 0 values have no
    usable ordering data, signalled by has_value=False so callers can place those
    cards last. Missing values resolve to +inf, which is also what frequency
    comparisons compare against."""
    if sort_val_str:
        try:
            val = float(sort_val_str)
            if val > 0:
                return val, True
        except ValueError:
            pass
    return float("inf"), False

# Katakana U+30A1..U+30F6 fold to hiragana by -0x60; everything else is unchanged.
# The exclusive range end keeps \u30f6 (U+30F6) inside and \u30f7 (U+30F7) out \u2014 exactly the
# old inclusive "\u30a1" <= c <= "\u30f6" per-char check, but at C speed via
# str.translate (this runs per card on the kana-normalized matching paths).
_KATA_TO_HIRA = {cp: cp - 0x60 for cp in range(0x30A1, 0x30F7)}

def to_hiragana(text: str) -> str:
    return text.translate(_KATA_TO_HIRA)

def is_kanji(ch: str) -> bool:
    """True if the single character is a CJK ideograph (Unified, Ext A,
    compatibility, or a supplementary-plane block like Ext B+)."""
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF      # CJK Unified
        or 0x3400 <= cp <= 0x4DBF   # Ext A
        or 0xF900 <= cp <= 0xFAFF   # compatibility ideographs
        or 0x20000 <= cp <= 0x3FFFF # supplementary planes (Ext B+)
    )

def parse_comparator(op: str) -> Callable[[float, float], bool]:
    """Returns a comparison function for the given operator string."""
    match op:
        case "=":
            return lambda a, b: a == b
        case "!=":
            return lambda a, b: a != b
        case "<":
            return lambda a, b: a < b
        case "<=":
            return lambda a, b: a <= b
        case ">":
            return lambda a, b: a > b
        case ">=":
            return lambda a, b: a >= b
        case _:
            raise ValueError(f"Unsupported operator: {op}")
