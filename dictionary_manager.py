import json
import os
from functools import lru_cache
from typing import Dict, Optional, Tuple, List

from .utils import to_hiragana

_MIN_PREFIX_LENGTH = 2
_COMBINED_MEMO_CAP = 50_000

class OccurrenceIndex:
    def __init__(self) -> None:
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}
        self.prefix_to_count: Dict[str, int] = {}

    def add(self, expression: str, reading: Optional[str], count: int) -> None:
        if reading:
            key = (expression, reading)
            self.expr_reading_to_count[key] = self.expr_reading_to_count.get(key, 0) + count

        # Always fallback to expression alone to account for reading mismatches
        # Accumulate counts for the same expression
        self.expr_to_count[expression] = self.expr_to_count.get(expression, 0) + count

    def add_prefixes(self, expression: str, count: int) -> None:
        for i in range(_MIN_PREFIX_LENGTH, len(expression)):
            prefix = expression[:i]
            self.prefix_to_count[prefix] = self.prefix_to_count.get(prefix, 0) + count

    def get(self, expression: str, reading: str) -> int:
        if (expression, reading) in self.expr_reading_to_count:
            return self.expr_reading_to_count[(expression, reading)]
        return self.expr_to_count.get(expression, 0)

    def get_combined(self, expression: str, reading: str) -> int:
        total = self.expr_to_count.get(expression, 0)
        if reading and reading != expression:
            total += self.expr_to_count.get(reading, 0)
        return total

    def get_with_prefix(self, expression: str, reading: str) -> int:
        return self.get(expression, reading) + self.prefix_to_count.get(expression, 0)

    def get_combined_with_prefix(self, expression: str, reading: str) -> int:
        total = self.get_combined(expression, reading)
        total += self.prefix_to_count.get(expression, 0)
        if reading and reading != expression:
            total += self.prefix_to_count.get(reading, 0)
        return total

class CombinedOccurrenceIndex:
    def __init__(self, dict_names: List[str], normalize_kana: bool = False, combine_word_forms: bool = False, prefix_matching: bool = False) -> None:
        self.dict_names = sorted(dict_names)
        self.normalize_kana = normalize_kana
        self.combine_word_forms = combine_word_forms
        self.prefix_matching = prefix_matching
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}

    def get(self, expression: str, reading: str) -> int:
        key = (expression, reading)
        if key in self.expr_reading_to_count:
            return self.expr_reading_to_count[key]

        total_count = 0
        use_prefix = self.prefix_matching and len(expression) >= _MIN_PREFIX_LENGTH
        for dict_name in self.dict_names:
            index = get_occurrence_index(dict_name, self.normalize_kana, self.prefix_matching)
            if self.combine_word_forms:
                count = index.get_combined_with_prefix(expression, reading) if use_prefix else index.get_combined(expression, reading)
            else:
                count = index.get_with_prefix(expression, reading) if use_prefix else index.get(expression, reading)
            total_count += count

        if len(self.expr_reading_to_count) >= _COMBINED_MEMO_CAP:
            self.expr_reading_to_count.clear()
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

@lru_cache(maxsize=32)
def _load_term_meta_raw(dict_name: str) -> Optional[list]:
    dir_path = _dict_dir(dict_name)
    index_path = _load_index_file(dir_path)
    if not index_path:
        return None
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        import traceback
        print(f"[priority-reorder] Failed to load {index_path}: {e}")
        traceback.print_exc()
        return None

def _build_index_from_raw(data: list, normalize_kana: bool = False, prefix_matching: bool = False) -> OccurrenceIndex:
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
            # (requires combine_word_forms at lookup time to credit kanji-bearing cards)
            effective_expression = reading if (is_kana_occurrences and reading) else expression
            if normalize_kana:
                effective_expression = to_hiragana(effective_expression)
                if reading:
                    reading = to_hiragana(reading)
            index.add(effective_expression, reading, count)
            if prefix_matching and len(effective_expression) > _MIN_PREFIX_LENGTH:
                index.add_prefixes(effective_expression, count)
    return index

@lru_cache(maxsize=64)
def get_occurrence_index(dict_name: str, normalize_kana: bool = False, prefix_matching: bool = False) -> OccurrenceIndex:
    data = _load_term_meta_raw(dict_name)
    if data is None:
        return OccurrenceIndex()
    return _build_index_from_raw(data, normalize_kana, prefix_matching)

@lru_cache(maxsize=32)
def get_combined_occurrence_index(dict_names_tuple: Tuple[str, ...], normalize_kana: bool = False, combine_word_forms: bool = False, prefix_matching: bool = False) -> CombinedOccurrenceIndex:
    sorted_dict_names = tuple(sorted(dict_names_tuple))
    return CombinedOccurrenceIndex(list(sorted_dict_names), normalize_kana, combine_word_forms, prefix_matching)
