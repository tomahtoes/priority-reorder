import bisect
import json
import os
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .utils import to_hiragana

_MIN_PREFIX_LENGTH = 2
_HONORIFIC_PREFIXES = ("お", "ご", "御")
_COMBINED_MEMO_CAP = 50_000

class OccurrenceIndex:
    def __init__(self) -> None:
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}
        self.honorific_to_count: Dict[str, int] = {}
        # Built lazily on first prefix query (see _ensure_prefix_index).
        self._prefix_exprs: Optional[List[str]] = None
        self._prefix_cumsum: List[int] = []

    def add(self, expression: str, reading: Optional[str], count: int) -> None:
        if reading:
            key = (expression, reading)
            self.expr_reading_to_count[key] = self.expr_reading_to_count.get(key, 0) + count

        # Always fallback to expression alone to account for reading mismatches
        # Accumulate counts for the same expression
        self.expr_to_count[expression] = self.expr_to_count.get(expression, 0) + count

    def _ensure_prefix_index(self) -> None:
        if self._prefix_exprs is not None:
            return
        items = sorted(self.expr_to_count.items())
        self._prefix_exprs = [expr for expr, _ in items]
        cumsum = [0]
        for _, count in items:
            cumsum.append(cumsum[-1] + count)
        self._prefix_cumsum = cumsum

    def prefix_total(self, expression: str) -> int:
        """Sum the counts of all terms that have ``expression`` as a *strict*
        prefix (longer terms only — the exact match is credited by ``get``).

        Computed via binary search over a lazily-built sorted index, so there is
        no per-term prefix explosion at build time."""
        if len(expression) < _MIN_PREFIX_LENGTH:
            return 0
        self._ensure_prefix_index()
        exprs = self._prefix_exprs
        # '￿' is the max BMP code point, so every term starting with
        # `expression` sorts before it (Japanese text never contains U+FFFF).
        lo = bisect.bisect_left(exprs, expression)
        hi = bisect.bisect_left(exprs, expression + "￿")
        if lo < len(exprs) and exprs[lo] == expression:
            lo += 1  # exclude the exact match (counted separately by get)
        return self._prefix_cumsum[hi] - self._prefix_cumsum[lo]

    def get(self, expression: str, reading: str) -> int:
        if (expression, reading) in self.expr_reading_to_count:
            return self.expr_reading_to_count[(expression, reading)]
        return self.expr_to_count.get(expression, 0)

    def get_total(
        self,
        expression: str,
        reading: str,
        *,
        combine_word_forms: bool = False,
        prefix_matching: bool = False,
        honorific_folding: bool = False,
    ) -> int:
        total = self.get(expression, reading)
        reading_is_distinct = bool(reading) and reading != expression
        if combine_word_forms and reading_is_distinct:
            total += self.expr_to_count.get(reading, 0)
        if prefix_matching:
            total += self.prefix_total(expression)
            if combine_word_forms and reading_is_distinct:
                total += self.prefix_total(reading)
        if honorific_folding:
            total += self.honorific_to_count.get(expression, 0)
            if combine_word_forms and reading_is_distinct:
                total += self.honorific_to_count.get(reading, 0)
        return total

class CombinedOccurrenceIndex:
    def __init__(self, dict_names: List[str], normalize_kana: bool = False, combine_word_forms: bool = False, prefix_matching: bool = False, honorific_folding: bool = False) -> None:
        self.dict_names = sorted(dict_names)
        self.normalize_kana = normalize_kana
        self.combine_word_forms = combine_word_forms
        self.prefix_matching = prefix_matching
        self.honorific_folding = honorific_folding
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}

    def get(self, expression: str, reading: str) -> int:
        key = (expression, reading)
        if key in self.expr_reading_to_count:
            return self.expr_reading_to_count[key]

        total_count = 0
        for dict_name in self.dict_names:
            index = get_occurrence_index(dict_name, self.normalize_kana, self.prefix_matching, self.honorific_folding)
            total_count += index.get_total(
                expression,
                reading,
                combine_word_forms=self.combine_word_forms,
                prefix_matching=self.prefix_matching,
                honorific_folding=self.honorific_folding,
            )

        memo = self.expr_reading_to_count
        # Bounded FIFO eviction (dicts preserve insertion order) instead of
        # clearing the whole memo, which would thrash when the working set
        # exceeds the cap.
        if len(memo) >= _COMBINED_MEMO_CAP:
            memo.pop(next(iter(memo)))
        memo[key] = total_count
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

def _build_index_from_raw(data: list, normalize_kana: bool = False, prefix_matching: bool = False, honorific_folding: bool = False) -> OccurrenceIndex:
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

    if honorific_folding:
        for expr, count in list(index.expr_to_count.items()):
            if not expr.startswith(_HONORIFIC_PREFIXES):
                continue
            # strip one-character honorific prefix (all entries in the tuple are single chars)
            stripped = expr[1:]
            if not stripped or stripped not in index.expr_to_count:
                continue
            index.honorific_to_count[stripped] = index.honorific_to_count.get(stripped, 0) + count

    return index

@lru_cache(maxsize=64)
def get_occurrence_index(dict_name: str, normalize_kana: bool = False, prefix_matching: bool = False, honorific_folding: bool = False) -> OccurrenceIndex:
    data = _load_term_meta_raw(dict_name)
    if data is None:
        return OccurrenceIndex()
    return _build_index_from_raw(data, normalize_kana, prefix_matching, honorific_folding)

@lru_cache(maxsize=32)
def get_combined_occurrence_index(dict_names_tuple: Tuple[str, ...], normalize_kana: bool = False, combine_word_forms: bool = False, prefix_matching: bool = False, honorific_folding: bool = False) -> CombinedOccurrenceIndex:
    sorted_dict_names = tuple(sorted(dict_names_tuple))
    return CombinedOccurrenceIndex(list(sorted_dict_names), normalize_kana, combine_word_forms, prefix_matching, honorific_folding)
