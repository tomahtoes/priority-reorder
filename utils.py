from typing import Callable, Any

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
