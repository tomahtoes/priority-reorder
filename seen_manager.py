"""`seen:N` support — a date-windowed occurrence lookup over the daily seen dicts in
``user_files/_seen/<YYYY-MM-DD>/term_meta_bank_*.json``.

Ported from the standalone daily-occurrence-search addon, but each day's dict is
parsed and counted through priority-reorder's own ``dictionary_manager``, so the
global occurrence flags (``prefix_matching``, ``kana_normalization``,
``combine_word_forms``, ``honorific_folding``) apply to ``seen:`` exactly as they do
to ``occurrences:`` — e.g. a 下駄 card is credited by a 下駄箱 entry when prefix
matching is on.

The current day's dict is rewritten while you immerse, so a day's index is cached
keyed on the source file's mtime (plus the build-time flags) and reloaded when either
changes. No background threads or TTL — the "lite" cache: each search re-stats the
day files, so new immersion and rollover are picked up automatically.

Top-level imports stay aqt-free (so this loads under pytest); the rollover hour is
read from the collection lazily inside ``_rollover_hour``.
"""

import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

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
# per-day index cache (lite: keyed on source mtime + build-time flags)
# ---------------------------------------------------------------------------

# (folder_name, mtime, normalize_kana, prefix_matching, honorific_folding) -> index.
# mtime self-invalidates on current-day rewrites; the build flags are in the key so a
# config change rebuilds. combine_word_forms is a *query-time* flag (applied in
# get_total), so it is deliberately not part of the build key.
_day_cache: Dict[Tuple, "dm.OccurrenceIndex"] = {}


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


def _day_index_for(folder, mtime, normalize_kana, prefix_matching, honorific_folding):
    """Cached ``OccurrenceIndex`` for one date folder at a known ``mtime``. Rebuilt when
    the mtime or the build-time flags change. A missing folder/file yields an empty
    index (treated as "no occurrences that day"). Splitting the mtime out lets
    ``get_window`` stat each day once and reuse it for both the index and the cache key."""
    key = (folder, mtime, normalize_kana, prefix_matching, honorific_folding)
    cached = _day_cache.get(key)
    if cached is not None:
        return cached

    # Drop any stale entry for this folder (old mtime / flag combo) so the current
    # day's repeated rewrites don't leak one cache entry per save.
    for stale in [k for k in _day_cache if k[0] == folder]:
        del _day_cache[stale]

    data = dm._load_term_meta_raw(_seen_dict_name(folder))
    index = (
        dm._build_index_from_raw(data, normalize_kana, prefix_matching, honorific_folding)
        if data is not None
        else dm.OccurrenceIndex()
    )
    _day_cache[key] = index
    return index


def _day_index(folder, normalize_kana, prefix_matching, honorific_folding):
    """Cached index for one date folder (stats the folder to get its mtime)."""
    return _day_index_for(folder, _source_mtime(folder), normalize_kana, prefix_matching, honorific_folding)


def _merge_window(day_indices):
    """Sum the day indices' ``expr_to_count`` and ``honorific_to_count`` into one
    merged ``OccurrenceIndex``. These are the *additive* maps behind every flag except
    the base ``get`` (prefix_total, combine's reading lookup, honorific folding), so a
    single merged binary search per note replaces one-per-day. The base exact ``get``
    is NOT merged — its per-day reading-mismatch fallback isn't additive (see
    ``window_total``)."""
    merged = dm.OccurrenceIndex()
    et, ht = merged.expr_to_count, merged.honorific_to_count
    for di in day_indices:
        for k, v in di.expr_to_count.items():
            et[k] = et.get(k, 0) + v
        for k, v in di.honorific_to_count.items():
            ht[k] = ht.get(k, 0) + v
    return merged


# Merged-window cache: signature (per-day (folder, mtime) + build flags) -> merged
# index. Bounded FIFO; the per-day _day_cache underneath does the actual file parsing.
_WINDOW_CACHE_CAP = 64
_window_cache: Dict[Tuple, "dm.OccurrenceIndex"] = {}


def clear_cache() -> None:
    """Drop the per-day and merged-window caches (tests; not needed at runtime — mtime
    keying self-invalidates)."""
    _day_cache.clear()
    _window_cache.clear()


# ---------------------------------------------------------------------------
# window resolution + lookup
# ---------------------------------------------------------------------------
#
# A search evaluates the same window over thousands of notes, so the window is resolved
# ONCE (get_window) before the note loop and then looked up per note (window_total),
# pure in-memory. The window is a pair: the per-day indices (for the exact base get,
# whose reading-mismatch fallback must stay per-day) and a single merged index (for the
# additive prefix/combine/honorific work — one binary search per note instead of N).

def get_window(
    n: int,
    normalize_kana: bool = False,
    prefix_matching: bool = False,
    honorific_folding: bool = False,
    today: Optional[date] = None,
):
    """Resolve the last ``n`` days (rollover-aware) to ``(day_indices, merged)``. Stats
    each day's file once; the merged index is cached by the window's (folder, mtime,
    flags) signature. ``today`` is injectable for tests."""
    if n <= 0:
        return [], dm.OccurrenceIndex()
    if today is None:
        today = today_date()
    folders = [date_to_folder(d) for d in window_dates(today, n)]
    mtimes = [_source_mtime(f) for f in folders]  # one stat per day
    day_indices = [
        _day_index_for(f, m, normalize_kana, prefix_matching, honorific_folding)
        for f, m in zip(folders, mtimes)
    ]
    sig = tuple(zip(folders, mtimes)) + (normalize_kana, prefix_matching, honorific_folding)
    merged = _window_cache.get(sig)
    if merged is None:
        merged = _merge_window(day_indices)
        if len(_window_cache) >= _WINDOW_CACHE_CAP:
            _window_cache.pop(next(iter(_window_cache)))
        _window_cache[sig] = merged
    return day_indices, merged


def window_mtimes(n: int, today: Optional[date] = None) -> Tuple:
    """The source mtimes of the last ``n`` daily dicts — a cheap fingerprint that
    changes whenever any day in the window is rewritten (notably today's dict during
    immersion). Used to invalidate full-scan result memos that ``mw.col.mod`` can't
    see, since the seen files change outside the collection."""
    if n <= 0:
        return ()
    if today is None:
        today = today_date()
    return tuple(_source_mtime(date_to_folder(d)) for d in window_dates(today, n))


def window_total(
    day_indices: List["dm.OccurrenceIndex"],
    merged: "dm.OccurrenceIndex",
    expression: str,
    reading: str,
    *,
    normalize_kana: bool = False,
    combine_word_forms: bool = False,
    prefix_matching: bool = False,
    honorific_folding: bool = False,
) -> int:
    """Window occurrence count for ``(expression, reading)`` (hybrid merge). Equals
    ``sum(day.get_total(...) for day in day_indices)`` exactly, but does the expensive
    prefix work ONCE over ``merged`` instead of once per day:

      * base exact ``get`` stays per-day (its reading-mismatch fallback is NOT additive,
        so it can't be merged without changing counts for homographs);
      * prefix_total / combine's reading lookup / honorific folding ARE additive over
        ``expr_to_count`` / ``honorific_to_count``, so they read from ``merged`` once.

    Pure in-memory — safe to call once per note."""
    if not day_indices:
        return 0
    if normalize_kana:
        expression = to_hiragana(expression)
        reading = to_hiragana(reading)

    total = 0
    for index in day_indices:                       # base exact get: per-day (exact)
        total += index.get(expression, reading)

    reading_is_distinct = bool(reading) and reading != expression
    if combine_word_forms and reading_is_distinct:
        total += merged.expr_to_count.get(reading, 0)
    if prefix_matching:
        total += merged.prefix_total(expression)
        if combine_word_forms and reading_is_distinct:
            total += merged.prefix_total(reading)
    if honorific_folding:
        total += merged.honorific_to_count.get(expression, 0)
        if combine_word_forms and reading_is_distinct:
            total += merged.honorific_to_count.get(reading, 0)
    return total


def window_count(
    n: int,
    expression: str,
    reading: str,
    *,
    normalize_kana: bool = False,
    combine_word_forms: bool = False,
    prefix_matching: bool = False,
    honorific_folding: bool = False,
    today: Optional[date] = None,
) -> int:
    """Resolve the window and look it up for a single ``(expression, reading)`` — a
    convenience wrapper over get_window + window_total. Per-note hot paths must NOT use
    this (it re-resolves/re-stats the window every call); resolve once with get_window
    and call window_total per note."""
    day_indices, merged = get_window(n, normalize_kana, prefix_matching, honorific_folding, today)
    return window_total(
        day_indices,
        merged,
        expression,
        reading,
        normalize_kana=normalize_kana,
        combine_word_forms=combine_word_forms,
        prefix_matching=prefix_matching,
        honorific_folding=honorific_folding,
    )
