"""`seen:N` support — a date-windowed *presence* lookup over the daily seen dicts in
``user_files/_seen/<YYYY-MM-DD>/term_meta_bank_*.json``.

`seen:N` is boolean: it matches a word that appears in ANY of the last ``N`` daily dicts. Each
day's dict is parsed into a membership set (not a counting index), and the global occurrence
flags (``prefix_matching``, ``kana_normalization``, ``combine_word_forms``, ``honorific_folding``)
apply to ``seen:`` exactly as they do to ``occurrences:`` — e.g. a 下駄 card is matched by a
下駄箱 entry when prefix matching is on. (Counts are deliberately not tracked: bare ``seen:N``
only asks "seen at all", so presence is all that is needed.)

The current day's dict is rewritten while you immerse, so a day's set is cached keyed on the
source file's mtime (plus the build-time flags) and reloaded when either changes. No background
threads or TTL — the "lite" cache: each search re-stats the day files, so new immersion and
rollover are picked up automatically.

Top-level imports stay aqt-free (so this loads under pytest); the rollover hour is read from the
collection lazily inside ``_rollover_hour``.
"""

import bisect
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

try:  # inside Anki: isolated package namespace
    from . import dictionary_manager as dm
    from .utils import to_hiragana
except ImportError:  # pytest / flat-import context
    import dictionary_manager as dm
    from utils import to_hiragana


# ---------------------------------------------------------------------------
# date / window helpers (pure — `now`/`rollover`/`today` injectable for tests)
# ---------------------------------------------------------------------------

def window_dates(today: date, n: int) -> List[date]:
    """The ``n`` calendar dates ending at (and including) ``today``, most-recent
    first. ``seen:1`` -> ``[today]``; ``seen:2`` -> ``[today, yesterday]``;
    ``n <= 0`` -> ``[]``."""
    if n <= 0:
        return []
    return [today - timedelta(days=i) for i in range(n)]


def date_to_folder(d: date) -> str:
    """Folder name for a date, matching the user's ``YYYY-MM-DD`` seen dict folders."""
    return d.strftime("%Y-%m-%d")


def _rollover_hour() -> int:
    """Anki's next-day rollover hour (default 4am), or 4 outside Anki."""
    try:
        from aqt import mw

        getter = getattr(mw.col, "get_config", None)
        if callable(getter):
            value = getter("rollover", 4)
        else:  # very old API
            value = mw.col.conf.get("rollover", 4)
        if isinstance(value, int):
            return value
    except Exception:
        pass
    return 4


def today_date(now: Optional[datetime] = None, rollover: Optional[int] = None) -> date:
    """The date that "today" maps to, honoring Anki's rollover hour.

    Before the rollover hour the previous calendar date is still "today", matching
    ``added:``/``edited:`` semantics. Args injectable for tests.
    """
    if now is None:
        now = datetime.now()
    if rollover is None:
        rollover = _rollover_hour()
    return (now - timedelta(hours=rollover)).date()


# ---------------------------------------------------------------------------
# boolean window structure + per-day build
# ---------------------------------------------------------------------------

class SeenWindow:
    """Window-wide *presence* of words across the resolved daily seen dicts. Holds the union of
    effective expressions seen in the window (``exprs``) and, for honorific folding, the set of
    stripped forms (``honorific_stripped``).

    ``contains`` is the boolean analogue of the old ``window_total(...) >= 1`` — the same base /
    combine / prefix / honorific paths, ORed."""

    def __init__(self, exprs: Set[str], honorific_stripped: Set[str]) -> None:
        self.exprs = exprs
        self.honorific_stripped = honorific_stripped
        # Sorted view of `exprs`, built lazily on the first prefix query (mirrors
        # OccurrenceIndex._ensure_prefix_index — no per-term prefix explosion at build time).
        self._sorted_exprs: Optional[List[str]] = None

    def _prefix_present(self, expression: str) -> bool:
        """True if some *strictly longer* term has ``expression`` as a prefix. Same binary-search
        bounds as ``OccurrenceIndex.prefix_total`` but returns presence (``lo < hi``) instead of
        a summed count."""
        if len(expression) < dm._MIN_PREFIX_LENGTH:
            return False
        if self._sorted_exprs is None:
            self._sorted_exprs = sorted(self.exprs)
        exprs = self._sorted_exprs
        # U+10FFFF is the max code point, so every term starting with `expression` sorts before
        # the sentinel — including terms whose next char is a supplementary-plane kanji.
        lo = bisect.bisect_left(exprs, expression)
        hi = bisect.bisect_left(exprs, expression + chr(0x10FFFF))
        if lo < len(exprs) and exprs[lo] == expression:
            lo += 1  # exclude the exact match (credited by base membership)
        return lo < hi

    def contains(
        self,
        expression: str,
        reading: str,
        *,
        normalize_kana: bool = False,
        combine_word_forms: bool = False,
        prefix_matching: bool = False,
        honorific_folding: bool = False,
    ) -> bool:
        """Whether ``(expression, reading)`` was seen anywhere in the window. Pure in-memory —
        safe to call once per note."""
        if normalize_kana:
            expression = to_hiragana(expression)
            reading = to_hiragana(reading)
        if expression in self.exprs:
            return True
        reading_is_distinct = bool(reading) and reading != expression
        if combine_word_forms and reading_is_distinct and reading in self.exprs:
            return True
        if prefix_matching:
            if self._prefix_present(expression):
                return True
            if combine_word_forms and reading_is_distinct and self._prefix_present(reading):
                return True
        if honorific_folding:
            if expression in self.honorific_stripped:
                return True
            if combine_word_forms and reading_is_distinct and reading in self.honorific_stripped:
                return True
        return False


def build_seen_day(
    data, normalize_kana: bool = False, honorific_folding: bool = False
) -> Tuple[Set[str], Set[str]]:
    """Parse one day's raw term_meta entries into ``(exprs, honorific_stripped)`` presence sets.

    Mirrors the entry parsing of ``dictionary_manager._build_index_from_raw`` (the ``count > 0``
    gate, the ``㋕`` kana-occurrence marker that attributes the entry to its reading, kana
    normalization), but records mere presence in a set instead of accumulating counts — base
    presence reduces to the expression set, so there is no ``(expr, reading)`` map. A drift-guard
    test pins this against the counting index's ``get_total(...) >= 1``."""
    exprs: Set[str] = set()
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
            # If marked as kana occurrences, attribute to the reading (a combine_word_forms
            # lookup then credits kanji-bearing cards with that reading).
            effective = reading if (is_kana_occurrences and reading) else expression
            if normalize_kana:
                effective = to_hiragana(effective)
            exprs.add(effective)

    honorific_stripped: Set[str] = set()
    if honorific_folding:
        for expr in exprs:
            if not expr.startswith(dm._HONORIFIC_PREFIXES):
                continue
            # strip one-character honorific prefix (all entries in the tuple are single chars)
            stripped = expr[1:]
            if stripped and stripped in exprs:
                honorific_stripped.add(stripped)
    return exprs, honorific_stripped


# ---------------------------------------------------------------------------
# per-day set cache (lite: keyed on source mtime + build-time flags)
# ---------------------------------------------------------------------------

# (folder_name, mtime, normalize_kana, honorific_folding) -> (exprs, honorific_stripped).
# mtime self-invalidates on current-day rewrites; the build flags are in the key so a config
# change rebuilds. prefix_matching / combine_word_forms are query-time flags (applied in
# SeenWindow.contains), so they are deliberately NOT part of the build key.
_day_cache: Dict[Tuple, Tuple[Set[str], Set[str]]] = {}


def _seen_dict_name(folder: str) -> str:
    """``dictionary_manager`` dict-name for one seen date folder — a nested path under
    user_files that ``_dict_dir``/``_load_term_meta_raw`` resolve transparently."""
    return f"{dm.SEEN_FOLDER}/{folder}"


def _source_mtime(folder: str) -> Optional[float]:
    """mtime of the day's term_meta file, or None if the folder/file is absent.
    Detects the current day's dict being rewritten while immersing."""
    path = dm._load_index_file(dm._dict_dir(_seen_dict_name(folder)))
    if not path:
        return None
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _seen_day_for(folder, mtime, normalize_kana, honorific_folding):
    """Cached presence sets ``(exprs, honorific_stripped)`` for one date folder at a known
    ``mtime``. Rebuilt when the mtime or the build-time flags change. A missing folder/file
    yields empty sets ("nothing seen that day"). Splitting the mtime out lets ``get_seen_window``
    stat each day once and reuse it for both the build and the cache key."""
    key = (folder, mtime, normalize_kana, honorific_folding)
    cached = _day_cache.get(key)
    if cached is not None:
        return cached

    # Drop any stale entry for this folder (old mtime / flag combo) so the current day's
    # repeated rewrites don't leak one cache entry per save.
    for stale in [k for k in _day_cache if k[0] == folder]:
        del _day_cache[stale]

    data = dm._load_term_meta_raw(_seen_dict_name(folder))
    sets = build_seen_day(data, normalize_kana, honorific_folding) if data is not None else (set(), set())
    _day_cache[key] = sets
    return sets


def _merge_seen_days(days) -> "SeenWindow":
    """Union the per-day ``(exprs, honorific_stripped)`` sets into one ``SeenWindow``. Presence is
    idempotent across days, so a plain union replaces the old additive count merge (and the
    per-day separation it needed for the non-additive reading-mismatch fallback)."""
    exprs: Set[str] = set()
    honorific: Set[str] = set()
    for de, dh in days:
        exprs |= de
        honorific |= dh
    return SeenWindow(exprs, honorific)


# Merged-window cache: signature (per-day (folder, mtime) + build flags) -> SeenWindow. Bounded
# FIFO; the per-day _day_cache underneath does the actual file parsing.
_WINDOW_CACHE_CAP = 64
_window_cache: Dict[Tuple, "SeenWindow"] = {}


def clear_cache() -> None:
    """Drop the per-day and merged-window caches (tests; not needed at runtime — mtime keying
    self-invalidates)."""
    _day_cache.clear()
    _window_cache.clear()


# ---------------------------------------------------------------------------
# window resolution + lookup
# ---------------------------------------------------------------------------
#
# A search evaluates the same window over thousands of notes, so the window is resolved ONCE
# (get_seen_window) before the note loop and then looked up per note (SeenWindow.contains), pure
# in-memory. With presence the window is a single merged membership set — no per-day separation,
# no additive sum — so each note is one set lookup (plus a binary search when prefix matching).

def get_seen_window(
    n: int,
    normalize_kana: bool = False,
    honorific_folding: bool = False,
    today: Optional[date] = None,
) -> "SeenWindow":
    """Resolve the last ``n`` days (rollover-aware) to a single ``SeenWindow``. Stats each day's
    file once; the window is cached by its (per-day folder+mtime, build flags) signature.
    ``prefix_matching`` is not a parameter — the union set is identical with or without it, and
    the sorted prefix view is built lazily on the returned object. ``today`` injectable for tests."""
    if n <= 0:
        return SeenWindow(set(), set())
    if today is None:
        today = today_date()
    folders = [date_to_folder(d) for d in window_dates(today, n)]
    mtimes = [_source_mtime(f) for f in folders]  # one stat per day
    sig = tuple(zip(folders, mtimes)) + (normalize_kana, honorific_folding)
    cached = _window_cache.get(sig)
    if cached is not None:
        return cached
    days = [_seen_day_for(f, m, normalize_kana, honorific_folding) for f, m in zip(folders, mtimes)]
    window = _merge_seen_days(days)
    if len(_window_cache) >= _WINDOW_CACHE_CAP:
        _window_cache.pop(next(iter(_window_cache)))
    _window_cache[sig] = window
    return window


def window_mtimes(n: int, today: Optional[date] = None) -> Tuple:
    """The source mtimes of the last ``n`` daily dicts — a cheap fingerprint that changes
    whenever any day in the window is rewritten (notably today's dict during immersion). Used to
    invalidate full-scan result memos that ``mw.col.mod`` can't see, since the seen files change
    outside the collection."""
    if n <= 0:
        return ()
    if today is None:
        today = today_date()
    return tuple(_source_mtime(date_to_folder(d)) for d in window_dates(today, n))
