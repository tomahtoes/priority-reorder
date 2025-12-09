"""
Handles Yomitan dictionary parsing and occurrence-based search patterns.
"""

import json
import os
import re
from functools import lru_cache
from typing import Dict, Optional, Tuple, Callable

from anki.notes import Note

from .config import SearchConfig

# Occurrence search pattern
OCC_PATTERN = re.compile(r"^occurrences:(?P<dict>[^=<>!]+)(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)$")

class OccurrenceIndex:
    def __init__(self) -> None:
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}

    def add(self, expression: str, reading: Optional[str], count: int) -> None:
        count_dict = self.expr_reading_to_count if reading else self.expr_to_count
        key = (expression, reading) if reading else expression
        if key not in count_dict or count < count_dict[key]:
            count_dict[key] = count

    def get(self, expression: str, reading: str) -> int:
        if (expression, reading) in self.expr_reading_to_count:
            return self.expr_reading_to_count[(expression, reading)]
        return self.expr_to_count.get(expression, 0)

def parse_operator(op: str) -> Callable[[int, int], bool]:
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

def _dict_dir(dict_name: str) -> str:
    return os.path.join(os.path.dirname(__file__), "user_files", dict_name)

def _load_index_file(dict_dir: str) -> Optional[str]:
    if not os.path.isdir(dict_dir):
        return None
    for name in os.listdir(dict_dir):
        if name.startswith("term_meta_bank_") and name.endswith(".json"):
            return os.path.join(dict_dir, name)
    return None

def _parse_term_meta_bank(path: str) -> OccurrenceIndex:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    index = OccurrenceIndex()
    for entry in data:
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        expression = entry[0]
        meta = entry[2] if isinstance(entry[2], dict) else {}
        reading = meta.get("reading") if isinstance(meta.get("reading"), str) else None
        count = 0
        freq_obj = meta.get("frequency")
        if isinstance(freq_obj, dict) and isinstance(freq_obj.get("value"), int):
            count = int(freq_obj["value"])
        elif isinstance(meta.get("value"), int):
            count = int(meta["value"])
        if isinstance(expression, str) and count > 0:
            index.add(expression, reading, count)
    return index

@lru_cache(maxsize=32)
def get_occurrence_index(dict_name: str) -> OccurrenceIndex:
    dir_path = _dict_dir(dict_name)
    index_path = _load_index_file(dir_path)
    if not index_path:
        return OccurrenceIndex()
    
    try:
        return _parse_term_meta_bank(index_path)
    except Exception:
        return OccurrenceIndex()

def _note_occurrence_count(note: Note, index: OccurrenceIndex, cfg: SearchConfig | None) -> int:
    try:
        if not cfg or not cfg.expression_field or not cfg.expression_reading_field:
            return 0
        if cfg.expression_field not in note or cfg.expression_reading_field not in note:
            return 0
        expr = note[cfg.expression_field]
        reading = note[cfg.expression_reading_field]
        if not expr or not reading:
            return 0
        return index.get(expr, reading)
    except Exception:
        return 0

def get_search_config() -> SearchConfig | None:
    from .config import get_current_config
    return get_current_config().search_config

def rewrite_search_string(search: str) -> str:
    if "occurrences:" not in search:
        return search
    
    try:
        from aqt import mw
        terms = search.split()
        new_terms = []
        
        for term in terms:
            if OCC_PATTERN.match(term):
                try:
                    m = OCC_PATTERN.match(term)
                    dict_name = m.group("dict").strip()
                    op = m.group("op")
                    thresh = int(m.group("thresh"))
                    
                    cfg = get_search_config()
                    if not cfg or (not cfg.expression_field or not cfg.expression_reading_field):
                        new_terms.append(term)
                        continue
                    
                    # all new cards with the required fields 
                    query = f"{cfg.expression_field}:* {cfg.expression_reading_field}:* is:new"
                    note_ids = mw.col.find_notes(query)
                    
                    index = get_occurrence_index(dict_name)
                    cmp_fn = parse_operator(op)
                    matching_nids = []
                    
                    for nid in note_ids:
                        note = mw.col.get_note(nid)
                        count = _note_occurrence_count(note, index, cfg)
                        if cmp_fn(count, thresh):
                            matching_nids.append(str(nid))
                    
                    if not matching_nids:
                        new_terms.append("nid:999999999")
                    else:
                        new_terms.append(f"nid:{','.join(matching_nids)}")
                        
                except Exception:
                    new_terms.append(term)
            else:
                new_terms.append(term)
        
        result = " ".join(new_terms)
        return result
    except Exception:
        # If anything goes wrong, return original search
        return search
