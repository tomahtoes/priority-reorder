import operator
import re
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

# The CJK ideograph ranges, defined once and consumed two ways below. Do not narrow them, and
# do not let a caller substitute its own pattern. A narrower `[一-龯]` leaves `kanji:num` and
# `kanji:new` blind to Ext A, the compatibility block (﨑 / 塚) and all of Ext B (𠮟), while
# variant matching still counts them. Defining both forms here is what stops the two
# drifting apart again.
_KANJI_RANGES = (
    (0x4E00, 0x9FFF),    # CJK Unified
    (0x3400, 0x4DBF),    # Ext A
    (0xF900, 0xFAFF),    # compatibility ideographs
    (0x20000, 0x3FFFF),  # supplementary planes (Ext B+)
)

def is_kanji(ch: str) -> bool:
    """True if the single character is a CJK ideograph (Unified, Ext A,
    compatibility, or a supplementary-plane block like Ext B+). Kept as explicit integer
    comparisons rather than a KANJI_RE match: this is called per character (see
    dictionary_manager._kanji_skeleton), where that is the cheaper form."""
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF      # CJK Unified
        or 0x3400 <= cp <= 0x4DBF   # Ext A
        or 0xF900 <= cp <= 0xFAFF   # compatibility ideographs
        or 0x20000 <= cp <= 0x3FFFF # supplementary planes (Ext B+)
    )

# is_kanji as a character class, for scanning a whole string in one C-level findall
# (kanji_manager). A test pins the two against each other.
KANJI_RE = re.compile(
    "[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _KANJI_RANGES) + "]"
)

# Plain dict of C-level operators rather than a chain of lambdas: faster on the per-card
# predicate paths, and free of the `match` statement, which is a SyntaxError before Python
# 3.10 (the rest of the addon is 3.9-compatible; see models.NoteData).
_COMPARATORS = {
    "=": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}

def parse_comparator(op: str) -> Callable[[float, float], bool]:
    """Returns a comparison function for the given operator string."""
    try:
        return _COMPARATORS[op]
    except KeyError:
        raise ValueError(f"Unsupported operator: {op}") from None
