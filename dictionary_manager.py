import bisect
import json
import os
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

try:  # inside Anki: isolated package namespace
    from .utils import is_kanji, to_hiragana
except ImportError:  # pytest / flat-import context
    from utils import is_kanji, to_hiragana

_MIN_PREFIX_LENGTH = 2
_MIN_SUFFIX_LENGTH = 2
_HONORIFIC_PREFIXES = ("お", "ご", "御")
_PHRASE_PARTICLES = frozenset("をがのにではもへと")

def _is_phrase_entry(expression: str, reading: Optional[str]) -> bool:
    """Structural test for the single-kanji phrase rule: kanji head, whitelisted
    particle second, non-empty tail, and a reading to validate against. Shared
    with seen_manager.build_seen_day so the counting and boolean sides can't drift."""
    return (
        bool(reading)
        and len(expression) >= 3
        and expression[1] in _PHRASE_PARTICLES
        and is_kanji(expression[0])
    )

def _is_suffix_phrase_entry(expression: str, reading: Optional[str]) -> bool:
    """Tail mirror of _is_phrase_entry for the single-kanji *suffix* phrase rule: kanji
    tail, whitelisted particle right before it, non-empty head, and a reading to validate
    against (母の日/ははのひ credits 日/ひ). Shared with seen_manager.build_seen_day so the
    counting and boolean sides can't drift."""
    return (
        bool(reading)
        and len(expression) >= 3
        and expression[-2] in _PHRASE_PARTICLES
        and is_kanji(expression[-1])
    )

def _honorific_fold_allowed(stripped: str, vocab) -> bool:
    """Whether an honorific-stripped remainder may be registered as a fold target.
    Allowed when the dict independently recognizes it, OR when it carries a kanji
    (お茶の間→茶の間, お金→金 — near-certainly the same lexeme). Kana-only strips
    stay gated on dict membership: that is where the unrelated-word junk lives
    (おかず→かず, おはよう→はよう). Shared with seen_manager.build_seen_day so the
    counting and boolean sides can't drift."""
    return bool(stripped) and (stripped in vocab or any(is_kanji(ch) for ch in stripped))

def _suffix_eligible(expression: str) -> bool:
    """Whether a card expression may receive *bare* suffix-matching credit. Gate:
    length >= 2 AND contains a kanji. That set is exactly "real words" — 2+ kanji terms
    (学校, 目的) and single-kanji-plus-okurigana words (食べる, 見る, 強い) — which carry
    their reading/meaning across compounds, while excluding bare single kanji (日/手/語,
    whose reading/meaning does not carry) and pure kana (する/こと/しい, katakana loanwords)
    that would match far too broadly. This is the tail mirror of _MIN_PREFIX_LENGTH; single
    kanji return only via the reading-validated tail phrase carve-out
    (single_kanji_suffix_phrase_total). Shared with the honorific-fold subsumption in
    get_total and the seen boolean twin so the sides can't drift."""
    return len(expression) >= _MIN_SUFFIX_LENGTH and any(is_kanji(ch) for ch in expression)
_COMBINED_MEMO_CAP = 50_000

# Reserved child folder under user_files holding the daily seen dicts
# (user_files/_seen/<YYYY-MM-DD>/term_meta_bank_*.json). The leading underscore keeps it
# visually distinct and sorted above real dictionaries. It is owned by the `seen:N`
# search term (see seen_manager.py) and must never be treated as a normal occurrence
# dictionary, so it is excluded from dict enumeration / expansion / updating.
SEEN_FOLDER = "_seen"

class OccurrenceIndex:
    def __init__(self) -> None:
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}
        self.honorific_to_count: Dict[str, int] = {}
        # Built lazily on first prefix query (see _ensure_prefix_index).
        self._prefix_exprs: Optional[List[str]] = None
        self._prefix_cumsum: List[int] = []
        # Built lazily on first suffix query (see _ensure_suffix_index): expressions sorted by
        # their reversed form, with a parallel cumsum — the suffix analogue of the prefix index
        # (d ends with e  <=>  reverse(d) starts with reverse(e)).
        self._suffix_revs: Optional[List[str]] = None
        self._suffix_cumsum: List[int] = []
        # Built lazily on first single-kanji phrase query (see _ensure_phrase_index).
        self._phrase_index: Optional[Dict[str, List[Tuple[str, str, int]]]] = None
        # Built lazily on first single-kanji suffix phrase query (see _ensure_suffix_phrase_index).
        self._suffix_phrase_index: Optional[Dict[str, List[Tuple[str, str, int]]]] = None

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
        # U+10FFFF is the max code point, so every term starting with `expression`
        # sorts before the sentinel — including terms whose next char is a
        # supplementary-plane kanji like 𠮟 (U+FFFF would sort before those).
        lo = bisect.bisect_left(exprs, expression)
        hi = bisect.bisect_left(exprs, expression + chr(0x10FFFF))
        if lo < len(exprs) and exprs[lo] == expression:
            lo += 1  # exclude the exact match (counted separately by get)
        return self._prefix_cumsum[hi] - self._prefix_cumsum[lo]

    def _ensure_suffix_index(self) -> None:
        if self._suffix_revs is not None:
            return
        # Sort by the reversed expression; the stored reversed keys are then in ascending
        # order, so bisect works exactly as it does over the (forward-sorted) prefix index.
        items = sorted(self.expr_to_count.items(), key=lambda kv: kv[0][::-1])
        self._suffix_revs = [expr[::-1] for expr, _ in items]
        cumsum = [0]
        for _, count in items:
            cumsum.append(cumsum[-1] + count)
        self._suffix_cumsum = cumsum

    def suffix_total(self, expression: str) -> int:
        """Sum the counts of all terms that have ``expression`` as a *strict* written
        suffix (longer terms only — the exact match is credited by ``get``), gated to
        kanji-bearing card expressions (see ``_suffix_eligible``).

        The suffix mirror of ``prefix_total``: a binary search over a lazily-built index of
        reversed expressions, since ``d`` ends with ``e`` iff ``reverse(d)`` starts with
        ``reverse(e)``. Same O(log n) cost, no per-term suffix explosion at build time."""
        if not _suffix_eligible(expression):
            return 0
        self._ensure_suffix_index()
        revs = self._suffix_revs
        rev = expression[::-1]
        # Same U+10FFFF sentinel trick as prefix_total, applied over the reversed strings, so
        # every term ending with `expression` sorts before the sentinel.
        lo = bisect.bisect_left(revs, rev)
        hi = bisect.bisect_left(revs, rev + chr(0x10FFFF))
        if lo < len(revs) and revs[lo] == rev:
            lo += 1  # exclude the exact match (counted separately by get)
        return self._suffix_cumsum[hi] - self._suffix_cumsum[lo]

    def _ensure_phrase_index(self) -> None:
        if self._phrase_index is not None:
            return
        index: Dict[str, List[Tuple[str, str, int]]] = {}
        for (expr, reading), count in self.expr_reading_to_count.items():
            if not _is_phrase_entry(expr, reading):
                continue
            index.setdefault(expr[0], []).append((expr[1], reading, count))
        self._phrase_index = index

    def single_kanji_phrase_total(self, expression: str, reading: str) -> int:
        """Phrase credit for a single-kanji card: sums entries 'X<particle><tail>'
        whose reading starts with the card's reading + the particle, validating
        that X is read in-context as the card reads it (手を貸す/てをかす credits
        手/て but not 手/しゅ). Complements prefix_total, which gates out
        single-character expressions entirely."""
        if len(expression) != 1 or not reading or not is_kanji(expression):
            return 0
        self._ensure_phrase_index()
        total = 0
        for particle, entry_reading, count in self._phrase_index.get(expression, ()):
            if entry_reading.startswith(reading + particle):
                total += count
        return total

    def _ensure_suffix_phrase_index(self) -> None:
        if self._suffix_phrase_index is not None:
            return
        index: Dict[str, List[Tuple[str, str, int]]] = {}
        for (expr, reading), count in self.expr_reading_to_count.items():
            if not _is_suffix_phrase_entry(expr, reading):
                continue
            index.setdefault(expr[-1], []).append((expr[-2], reading, count))  # bucket by LAST char
        self._suffix_phrase_index = index

    def single_kanji_suffix_phrase_total(self, expression: str, reading: str) -> int:
        """Tail mirror of single_kanji_phrase_total: phrase credit for a single-kanji card
        from entries '<head><particle>X' whose reading ends with the particle + the card's
        reading, validating that X is read in-context as the card reads it (母の日/ははのひ
        credits 日/ひ but not 日/にち). An EXACT match — particles sit on a word boundary, so
        the tail kanji never rendaku's. Complements suffix_total, which gates out
        single-character expressions entirely."""
        if len(expression) != 1 or not reading or not is_kanji(expression):
            return 0
        self._ensure_suffix_phrase_index()
        total = 0
        for particle, entry_reading, count in self._suffix_phrase_index.get(expression, ()):
            if entry_reading.endswith(particle + reading):
                total += count
        return total

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
        suffix_matching: bool = False,
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
            total += self.single_kanji_phrase_total(expression, reading)
        if suffix_matching:
            # No reading-side term (unlike prefix): a kana reading is never suffix-eligible
            # (contains no kanji), so suffix_total(reading) is a definitional no-op.
            total += self.suffix_total(expression)
            # Single-kanji cards (gated out of the bare path) return only via the tail phrase
            # carve-out — the mirror of single_kanji_phrase_total.
            total += self.single_kanji_suffix_phrase_total(expression, reading)
        if honorific_folding:
            # honorific_to_count credits the bare form from an 'お/ご/御 + form' entry, which is
            # a strict written suffix of that entry — so when the expression is suffix-eligible,
            # suffix_total(expression) already counted it. Skip the expression-side term then to
            # avoid double-counting. Kana-only folds (かず←おかず) are not suffix-eligible, so they
            # are never subsumed and keep their credit; the reading side is kana-tailed likewise.
            if not (suffix_matching and _suffix_eligible(expression)):
                total += self.honorific_to_count.get(expression, 0)
            if combine_word_forms and reading_is_distinct:
                total += self.honorific_to_count.get(reading, 0)
        return total

class CombinedOccurrenceIndex:
    def __init__(self, dict_names: List[str], normalize_kana: bool = False, combine_word_forms: bool = False, prefix_matching: bool = False, suffix_matching: bool = False, honorific_folding: bool = False) -> None:
        self.dict_names = sorted(dict_names)
        self.normalize_kana = normalize_kana
        self.combine_word_forms = combine_word_forms
        self.prefix_matching = prefix_matching
        self.suffix_matching = suffix_matching
        self.honorific_folding = honorific_folding
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}

    def get(self, expression: str, reading: str) -> int:
        key = (expression, reading)
        if key in self.expr_reading_to_count:
            return self.expr_reading_to_count[key]

        total_count = 0
        for dict_name in self.dict_names:
            index = get_occurrence_index(dict_name, self.normalize_kana, self.prefix_matching, self.suffix_matching, self.honorific_folding)
            total_count += index.get_total(
                expression,
                reading,
                combine_word_forms=self.combine_word_forms,
                prefix_matching=self.prefix_matching,
                suffix_matching=self.suffix_matching,
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
    """Returns a sorted list of all dictionary names in user_files, ignoring 'all',
    the reserved '_seen' folder, and dot-prefixed temp dirs (updater swap leftovers)."""
    user_files_dir = os.path.join(os.path.dirname(__file__), "user_files")
    if not os.path.isdir(user_files_dir):
        return []

    dict_names = []
    for item in os.listdir(user_files_dir):
        if item == "all" or item == SEEN_FOLDER or item.startswith("."):
            continue
        item_path = os.path.join(user_files_dir, item)
        if os.path.isdir(item_path):
            dict_names.append(item)
    return sorted(dict_names)

def _load_term_meta_raw(dict_name: str) -> Optional[list]:
    """Parse the dictionary's term meta bank from disk. Deliberately NOT cached:
    the raw list is huge (every entry of a 100k+ term bank) and only needed while
    building an OccurrenceIndex — get_occurrence_index memoizes the compact result,
    so in the steady state each dict is parsed once per session and the raw list
    is garbage-collected right after the build."""
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
            if not _honorific_fold_allowed(stripped, index.expr_to_count):
                continue
            index.honorific_to_count[stripped] = index.honorific_to_count.get(stripped, 0) + count

    return index

@lru_cache(maxsize=64)
def get_occurrence_index(dict_name: str, normalize_kana: bool = False, prefix_matching: bool = False, suffix_matching: bool = False, honorific_folding: bool = False) -> OccurrenceIndex:
    # suffix_matching is build-irrelevant (the reversed index is lazy, like the prefix one), but
    # is part of the cache key to mirror prefix_matching — a flag flip yields a fresh index.
    data = _load_term_meta_raw(dict_name)
    if data is None:
        return OccurrenceIndex()
    return _build_index_from_raw(data, normalize_kana, prefix_matching, honorific_folding)

@lru_cache(maxsize=32)
def get_combined_occurrence_index(dict_names_tuple: Tuple[str, ...], normalize_kana: bool = False, combine_word_forms: bool = False, prefix_matching: bool = False, suffix_matching: bool = False, honorific_folding: bool = False) -> CombinedOccurrenceIndex:
    sorted_dict_names = tuple(sorted(dict_names_tuple))
    return CombinedOccurrenceIndex(list(sorted_dict_names), normalize_kana, combine_word_forms, prefix_matching, suffix_matching, honorific_folding)

def expand_dict_names(dict_str: str) -> List[str]:
    """Resolve the dict spec of an ``occurrences:`` term to a de-duplicated list of
    dictionary names. Accepts a single name (``Foo``), a bracketed combinator
    (``[A,B,C]``), and the ``all`` keyword (expands to every dict in user_files)."""
    if dict_str.startswith('[') and dict_str.endswith(']'):
        raw_names = [d.strip() for d in dict_str[1:-1].split(',')]
    else:
        raw_names = [dict_str]

    dict_names: List[str] = []
    for name in raw_names:
        if name == "all":
            dict_names.extend(get_all_dict_names())
        else:
            dict_names.append(name)

    # Remove duplicates to act as a true combinator (preserves first-seen order), and
    # drop the reserved '_seen' folder so `occurrences:_seen` / `occurrences:[A,_seen]`
    # can never reach the daily seen dicts — only `seen:N` may.
    return [name for name in dict.fromkeys(dict_names) if name != SEEN_FOLDER]

def occurrence_count(
    dict_names: List[str],
    expression: str,
    reading: str,
    *,
    normalize_kana: bool = False,
    combine_word_forms: bool = False,
    prefix_matching: bool = False,
    suffix_matching: bool = False,
    honorific_folding: bool = False,
) -> int:
    """Total occurrence count for ``(expression, reading)`` across ``dict_names``,
    honoring all five lookup flags. Mirrors the body of the former
    ``OccurrenceRule.matches`` so both the reorder path and the browser/API search
    term resolve identically. Callers must ensure expression/reading are present;
    a note missing either should be treated as a non-match upstream rather than
    fed a 0 here."""
    if normalize_kana:
        expression = to_hiragana(expression)
        reading = to_hiragana(reading)

    if len(dict_names) == 1:
        index = get_occurrence_index(dict_names[0], normalize_kana, prefix_matching, suffix_matching, honorific_folding)
        return index.get_total(
            expression,
            reading,
            combine_word_forms=combine_word_forms,
            prefix_matching=prefix_matching,
            suffix_matching=suffix_matching,
            honorific_folding=honorific_folding,
        )

    combined_index = get_combined_occurrence_index(
        tuple(dict_names), normalize_kana, combine_word_forms, prefix_matching, suffix_matching, honorific_folding
    )
    return combined_index.get(expression, reading)
