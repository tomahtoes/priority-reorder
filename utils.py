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

def to_hiragana(text: str) -> str:
    return "".join(
        chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c
        for c in text
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
