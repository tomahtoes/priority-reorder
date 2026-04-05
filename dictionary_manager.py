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
            key = (expression, reading)
            self.expr_reading_to_count[key] = self.expr_reading_to_count.get(key, 0) + count
            
        # Always fallback to expression alone to account for reading mismatches
        # Accumulate counts for the same expression
        self.expr_to_count[expression] = self.expr_to_count.get(expression, 0) + count

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

def get_all_dict_names() -> List[str]:
    """Returns a sorted list of all dictionary names in user_files, ignoring 'all'."""
    user_files_dir = os.path.join(os.path.dirname(__file__), "user_files")
    if not os.path.isdir(user_files_dir):
        return []
    
    dict_names = []
    for item in os.listdir(user_files_dir):
        if item == "all":
            continue
        item_path = os.path.join(user_files_dir, item)
        if os.path.isdir(item_path):
            dict_names.append(item)
    return sorted(dict_names)

def _parse_term_meta_bank(path: str) -> OccurrenceIndex:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    index = OccurrenceIndex()
    for entry in data:
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        expression = entry[0]
        meta = entry[2]
        reading = None
        count = 0
        is_kana_occurrences = False

        if isinstance(meta, dict):
            reading = meta.get("reading") if isinstance(meta.get("reading"), str) else None
            
            # Check for '㋕' (kana-only indicator) in display values
            display_val = str(meta.get("displayValue", ""))
            freq_obj = meta.get("frequency")
            if isinstance(freq_obj, dict):
                display_val += str(freq_obj.get("displayValue", ""))
                if isinstance(freq_obj.get("value"), int):
                    count = int(freq_obj["value"])
            
            if "㋕" in display_val:
                is_kana_occurrences = True
            
            if count == 0 and isinstance(meta.get("value"), int):
                count = int(meta["value"])
        elif isinstance(meta, int):
            count = meta
        elif isinstance(meta, str):
            try:
                count = int(meta)
            except ValueError:
                pass

        if isinstance(expression, str) and count > 0:
            # If specifically marked as kana occurrences, attribute to the reading
            effective_expression = reading if (is_kana_occurrences and reading) else expression
            index.add(effective_expression, reading, count)
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
