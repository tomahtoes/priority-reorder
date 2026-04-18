from typing import Callable, Any

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
