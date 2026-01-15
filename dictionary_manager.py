import json
import os
from functools import lru_cache
from typing import Dict, Optional, Tuple, List

class OccurrenceIndex:
    def __init__(self) -> None:
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}

    def add(self, expression: str, reading: Optional[str], count: int) -> None:
        if reading:
            self.expr_reading_to_count[(expression, reading)] = count
        else:
            self.expr_to_count[expression] = count

    def get(self, expression: str, reading: str) -> int:
        if (expression, reading) in self.expr_reading_to_count:
            return self.expr_reading_to_count[(expression, reading)]
        return self.expr_to_count.get(expression, 0)

class CombinedOccurrenceIndex:    
    def __init__(self, dict_names: List[str]) -> None:
        self.dict_names = sorted(dict_names)
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}
    
    def get(self, expression: str, reading: str) -> int:
        key = (expression, reading)
        if key in self.expr_reading_to_count:
            return self.expr_reading_to_count[key]
        
        total_count = 0
        for dict_name in self.dict_names:
            index = get_occurrence_index(dict_name)
            count = index.get(expression, reading)
            total_count += count
        
        self.expr_reading_to_count[key] = total_count
        return total_count

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

@lru_cache(maxsize=16)
def get_combined_occurrence_index(dict_names_tuple: Tuple[str, ...]) -> CombinedOccurrenceIndex:
    sorted_dict_names = tuple(sorted(dict_names_tuple))
    return CombinedOccurrenceIndex(list(sorted_dict_names))
