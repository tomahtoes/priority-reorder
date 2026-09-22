"""`seen:N` support, a date-windowed *presence* lookup over the daily seen dicts in
``user_files/_seen/<YYYY-MM-DD>/term_meta_bank_*.json``.

`seen:N` is boolean. It matches a word appearing in ANY of the last ``N`` daily dicts. Each day
is parsed into a membership set rather than a counting index, and every global occurrence flag
applies to ``seen:`` exactly as it does to ``occurrences:``, so a 下駄 card is matched by a 下駄箱
entry under prefix matching, 箱 by the same entry under suffix matching, 煌めく by 煌く under
variant matching, and 戒める by 戒め under stem matching. Counts are deliberately not tracked,
since bare ``seen:N`` only asks "seen at all".

Each rule here is the boolean twin of a counting rule in dictionary_manager, and the structural
helpers are imported from there rather than reimplemented. Editing one side without the other
makes the two drift. A drift-guard test pins them against each other.

The current day's dict is rewritten while you immerse, so a day's set is cached on the source
file's mtime plus the build-time flags, and reloaded when either changes. No background threads
or TTL. Each search re-stats the day files, so new immersion and rollover are picked up
automatically.

Top-level imports stay aqt-free so this loads under pytest. The rollover hour is read from the
collection lazily inside ``_rollover_hour``.
"""

import bisect
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

try:  # inside Anki: isolated package namespace
    from . import dictionary_manager as dm
    from .utils import is_kanji, to_hiragana
except ImportError:  # pytest / flat-import context
    import dictionary_manager as dm
    from utils import is_kanji, to_hiragana


# The date helpers below are pure; `now`, `rollover` and `today` are injectable for tests.

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


class SeenWindow:
    """Window-wide *presence* of words across the resolved daily seen dicts, as unions over the
    window:

      ``exprs``                  every effective expression seen
      ``honorific_stripped``     stripped forms, for honorific folding
      ``phrase_entries``         particle-phrase entries with a kanji head
      ``suffix_phrase_entries``  particle-phrase entries with a kanji tail
      ``suru_entries``           'X<する|じる|ずる>' entries
      ``variant_entries``        every kanji-bearing entry
      ``stem_entries``           every entry whose reading differs from its written form
      ``negative_entries``       entries ending in ず/ぬ

    Every set after ``honorific_stripped`` holds ``(expression, reading)`` pairs so a reading can be compared at query time.

    Each ``_*_present`` method below is the boolean analogue of the same-named ``*_total`` on
    OccurrenceIndex, returning presence instead of a sum. ``contains`` ORs them all."""

    def __init__(
        self,
        exprs: Set[str],
        honorific_stripped: Set[str],
        phrase_entries: Set[Tuple[str, str]],
        suffix_phrase_entries: Set[Tuple[str, str]],
        suru_entries: Set[Tuple[str, str]],
        variant_entries: Set[Tuple[str, str]],
        stem_entries: Set[Tuple[str, str]],
        negative_entries: Set[Tuple[str, str]],
    ) -> None:
        self.exprs = exprs
        self.honorific_stripped = honorific_stripped
        self.phrase_entries = phrase_entries
        self.suffix_phrase_entries = suffix_phrase_entries
        self.suru_entries = suru_entries
        self.variant_entries = variant_entries
        self.stem_entries = stem_entries
        self.negative_entries = negative_entries
        # Lazy views, each built by its own _*_present method on the first query that needs it
        # and shaped like the matching OccurrenceIndex._ensure_* index.
        self._sorted_exprs: Optional[List[str]] = None
        # Distinct from `_sorted_exprs` and cannot be reused: reversed, then sorted.
        self._sorted_revs: Optional[List[str]] = None
        self._phrase_by_first: Optional[Dict[str, List[Tuple[str, str]]]] = None
        self._suffix_phrase_by_last: Optional[Dict[str, List[Tuple[str, str]]]] = None
        self._suru_by_first: Optional[Dict[str, List[Tuple[str, str]]]] = None
        # Bare expressions, no cached skeleton.
        self._variant_by_reading: Optional[Dict[str, List[str]]] = None
        # Stem matching has no view. `stem_entries` is already keyed as the exact
        # (expression, reading) pairs _stem_candidates probes for, so there is nothing to reshape.
        # Compound matching sweeps those same pairs by prefix, so it sorts them.
        self._sorted_stem_entries: Optional[List[Tuple[str, str]]] = None
        self._sorted_stem_exprs: List[str] = []
        self._sorted_negative_entries: Optional[List[Tuple[str, str]]] = None
        self._sorted_negative_revs: List[str] = []

    def _prefix_present(self, expression: str) -> bool:
        """True if some *strictly longer* term has ``expression`` as a prefix. Same binary-search
        bounds as ``OccurrenceIndex.prefix_total``, returning ``lo < hi`` instead of a sum."""
        if len(expression) < dm._MIN_PREFIX_LENGTH:
            return False
        if self._sorted_exprs is None:
            self._sorted_exprs = sorted(self.exprs)
        exprs = self._sorted_exprs
        # Sentinel must be U+10FFFF, the max code point. U+FFFF would sort before terms whose
        # next char is a supplementary-plane kanji, silently missing them.
        lo = bisect.bisect_left(exprs, expression)
        hi = bisect.bisect_left(exprs, expression + chr(0x10FFFF))
        if lo < len(exprs) and exprs[lo] == expression:
            lo += 1  # exclude the exact match (credited by base membership)
        return lo < hi

    def _suffix_present(self, expression: str) -> bool:
        """True if some *strictly longer* term has ``expression`` as a written suffix. The suffix
        analogue of ``_prefix_present``, over a reversed-sorted view of ``exprs``, gated to
        kanji-bearing expressions (``dm._suffix_eligible``)."""
        if not dm._suffix_eligible(expression):
            return False
        if self._sorted_revs is None:
            self._sorted_revs = sorted(e[::-1] for e in self.exprs)
        revs = self._sorted_revs
        rev = expression[::-1]
        lo = bisect.bisect_left(revs, rev)
        hi = bisect.bisect_left(revs, rev + chr(0x10FFFF))
        if lo < len(revs) and revs[lo] == rev:
            lo += 1  # exclude the exact match (credited by base membership)
        return lo < hi

    def _negative_suffix_present(self, expression: str, reading: str) -> bool:
        """True if some entry ends in the card's 未然形 + ず/ぬ on both sides (にも拘わらず finds
        拘わる). The presence analogue of ``OccurrenceIndex.negative_suffix_total``.

        Mirrors the skip of entries starting with the card, which nothing else credits when
        prefix matching is off. Drops the compound dedup guard, as ``_variant_present`` drops
        its own: whatever it skips, ``_stem_compound_present`` already answers True for."""
        if not dm._suffix_eligible(expression):
            return False
        forms = dm._negative_forms(expression, reading)
        if not forms:
            return False
        if self._sorted_negative_entries is None:
            rows = sorted((expr[::-1], (expr, entry_reading))
                          for expr, entry_reading in self.negative_entries)
            self._sorted_negative_entries = [key for _rev, key in rows]
            self._sorted_negative_revs = [rev for rev, _key in rows]
        entries = self._sorted_negative_entries
        revs = self._sorted_negative_revs
        for form_expr, form_reading in forms:
            rev = form_expr[::-1]
            lo = bisect.bisect_left(revs, rev)
            hi = bisect.bisect_left(revs, rev + chr(0x10FFFF))
            for position in range(lo, hi):
                entry_expr, entry_reading = entries[position]
                if entry_reading.endswith(form_reading) and not entry_expr.startswith(expression):
                    return True
        return False

    def _phrase_present(self, expression: str, reading: str) -> bool:
        """True if some particle-phrase entry 'X<particle>' (tail optional) validates the
        single-kanji card's reading."""
        if len(expression) != 1 or not reading or not is_kanji(expression):
            return False
        if self._phrase_by_first is None:
            by_first: Dict[str, List[Tuple[str, str]]] = {}
            for expr, entry_reading in self.phrase_entries:
                by_first.setdefault(expr[0], []).append((expr[1], entry_reading))
            self._phrase_by_first = by_first
        return any(
            entry_reading.startswith(reading + particle)
            for particle, entry_reading in self._phrase_by_first.get(expression, ())
        )

    def _suffix_phrase_present(self, expression: str, reading: str) -> bool:
        """True if some tail particle-phrase entry '<head><particle>X' validates the single-kanji
        card's reading."""
        if len(expression) != 1 or not reading or not is_kanji(expression):
            return False
        if self._suffix_phrase_by_last is None:
            by_last: Dict[str, List[Tuple[str, str]]] = {}
            for expr, entry_reading in self.suffix_phrase_entries:
                by_last.setdefault(expr[-1], []).append((expr[-2], entry_reading))
            self._suffix_phrase_by_last = by_last
        return any(
            entry_reading.endswith(particle + reading)
            for particle, entry_reading in self._suffix_phrase_by_last.get(expression, ())
        )

    def _suru_present(self, expression: str, reading: str) -> bool:
        """True if some entry 'X<する|じる|ずる>' reads as the single-kanji card's reading plus
        that suffix."""
        if len(expression) != 1 or not reading or not is_kanji(expression):
            return False
        if self._suru_by_first is None:
            by_first: Dict[str, List[Tuple[str, str]]] = {}
            for expr, entry_reading in self.suru_entries:
                by_first.setdefault(expr[0], []).append((expr[1:], entry_reading))
            self._suru_by_first = by_first
        return any(
            dm._suru_reading_matches(reading, entry_reading, suffix)
            for suffix, entry_reading in self._suru_by_first.get(expression, ())
        )

    def _variant_present(self, expression: str, reading: str, card_kanji: Optional[str] = None) -> bool:
        """True if some entry with the identical reading is another written form of the same word
        (kanji sets nest; see ``dm._variant_kanji_compatible``).

        No prefix/suffix dedup guard, unlike the counting side. Presence is idempotent, so there
        is nothing to double-count, and any candidate ``variant_total`` skips as already-credited
        is one ``_prefix_present`` or ``_suffix_present`` would answer True for anyway.

        The buckets hold bare expressions, so skeletons are derived inside the ``any(...)`` for
        the one or two candidates a query touches rather than for every entry at build time. Over
        a seen:1 + seen:7 + seen:30 reorder that halves the per-window prep, and ``any``
        short-circuits on the first hit.

        Reads ``variant_entries``, which ``build_seen_day`` only fills under ``variant_matching``,
        so this answers False on a window built without it. Callers pass the one config flag to
        both ``get_seen_window`` and ``contains``, keeping the two in step.

        ``card_kanji`` is an optional precomputed ``_kanji_skeleton(expression)``, so a card
        checked against several windows does not re-derive it per window. It cannot change the
        result. Left None, it is derived here."""
        if card_kanji is None:
            card_kanji = dm._kanji_skeleton(expression)
        if not card_kanji or not reading:
            return False
        if self._variant_by_reading is None:
            by_reading: Dict[str, List[str]] = {}
            for expr, entry_reading in self.variant_entries:
                by_reading.setdefault(entry_reading, []).append(expr)
            self._variant_by_reading = by_reading
        return any(
            expr != expression
            and dm._variant_kanji_compatible(card_kanji, dm._kanji_skeleton(expr))
            for expr in self._variant_by_reading.get(reading, ())
        )

    def _stem_present(
        self, expression: str, reading: str, combine_word_forms: bool = False
    ) -> bool:
        """True if the card's 連用形 or its さ/み/げ nominalization is present (戒める finds 戒め,
        遊ぶ finds 遊び, 強い finds 強さ).

        Must mirror BOTH of the counting side's terms, which is what the cross-module drift guard
        pins: the ``(expression, reading)`` pair in ``stem_entries``, and under
        ``combine_word_forms`` the candidate's bare reading in ``exprs``. Dropping the second
        silently diverges from the counting side on any flagset combining stem with
        combine_word_forms.

        Reads ``stem_entries``, which ``build_seen_day`` only fills under ``stem_matching``, so
        this answers False on a window built without it. Callers pass the one config flag to both
        ``get_seen_window`` and ``contains``, keeping the two in step.

        Probes ``stem_entries`` directly, since it is already keyed the way the candidates are.
        The one rule with no lazy view on either side."""
        candidates = dm._stem_candidates(expression, reading)
        if not candidates:
            return False
        pairs = self.stem_entries
        for cand in candidates:
            if cand in pairs:
                return True
        if combine_word_forms:
            for _cand_expr, cand_reading in candidates:
                if cand_reading in self.exprs:
                    return True
        return False

    def _stem_compound_present(
        self, expression: str, reading: str,
        card_kanji: Optional[str] = None,
        stem_matching: bool = False,
        variant_matching: bool = False,
    ) -> bool:
        """True if some entry compounds on the card's stem (奮う finds 奮い立つ, 取る finds
        取り消す). The presence analogue of ``OccurrenceIndex.stem_compound_total``, over
        ``stem_entries`` sorted by expression.

        Every gate the counting side applies MUST be mirrored here, unlike ``_variant_present``,
        which drops its dedup guards because anything variant_total skips ``_prefix_present``
        already answers True for. Here they are the difference between zero and non-zero rather
        than between two non-zero totals: the counting rule concedes entries beginning with the
        card expression to prefix_total unconditionally, so with ``prefix_matching`` off nothing
        credits them, the total is 0, and presence must be False too.

        The candidate nesting skip is the one thing not mirrored: it exists to stop the counting side adding the same entry twice, which
        presence is immune to. Reads ``stem_entries``, which ``build_seen_day`` fills under
        ``stem_matching`` OR ``compound_matching``, so this answers False on a window built
        without either. Its ``reading != effective`` build gate is exactly the counting rule's
        kana-pair gate, which is what keeps the two sides identical."""
        candidates = dm._stem_candidates(expression, reading)
        if not candidates:
            return False
        if self._sorted_stem_entries is None:
            self._sorted_stem_entries = sorted(self.stem_entries)
            self._sorted_stem_exprs = [expr for expr, _reading in self._sorted_stem_entries]
        entries = self._sorted_stem_entries
        exprs = self._sorted_stem_exprs
        if card_kanji is None and variant_matching:
            card_kanji = dm._kanji_skeleton(expression)
        for cand_expr, cand_reading in candidates:
            # Same U+10FFFF sentinel as _prefix_present.
            lo = bisect.bisect_left(exprs, cand_expr)
            hi = bisect.bisect_left(exprs, cand_expr + chr(0x10FFFF))
            for position in range(lo, hi):
                entry_expr, entry_reading = entries[position]
                if entry_expr.startswith(expression):
                    continue
                if not entry_reading.startswith(cand_reading):
                    continue
                if stem_matching and entry_expr == cand_expr and entry_reading == cand_reading:
                    continue
                if (variant_matching and entry_reading == reading and card_kanji
                        and dm._variant_kanji_compatible(card_kanji,
                                                         dm._kanji_skeleton(entry_expr))):
                    continue
                return True
        return False

    def contains(
        self,
        expression: str,
        reading: str,
        *,
        normalize_kana: bool = False,
        combine_word_forms: bool = False,
        prefix_matching: bool = False,
        suffix_matching: bool = False,
        variant_matching: bool = False,
        stem_matching: bool = False,
        compound_matching: bool = False,
        honorific_folding: bool = False,
        prefolded: bool = False,
        card_kanji: Optional[str] = None,
    ) -> bool:
        """Whether ``(expression, reading)`` was seen anywhere in the window. Purely in memory,
        so it is safe to call once per note.

        ``prefolded`` says the caller already applied ``to_hiragana`` to both strings, making the
        fold here a pure re-allocation. ``card_kanji`` is a precomputed skeleton passed through to
        ``_variant_present``. Both are for callers evaluating one card against several windows,
        and neither changes the answer. See ``DataManager._note_derived``."""
        if normalize_kana and not prefolded:
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
            if self._phrase_present(expression, reading):
                return True
            if self._suru_present(expression, reading):
                return True
        if suffix_matching:
            # No reading-side term. A kana reading is never suffix-eligible (contains no kanji).
            if self._suffix_present(expression):
                return True
            if self._suffix_phrase_present(expression, reading):
                return True
            if self._negative_suffix_present(expression, reading):
                return True
        if variant_matching and self._variant_present(expression, reading, card_kanji):
            return True
        if stem_matching and self._stem_present(expression, reading, combine_word_forms):
            return True
        if compound_matching and self._stem_compound_present(
            expression, reading, card_kanji, stem_matching, variant_matching
        ):
            return True
        if honorific_folding:
            if expression in self.honorific_stripped:
                return True
            if combine_word_forms and reading_is_distinct and reading in self.honorific_stripped:
                return True
        return False


def build_seen_day(
    data, normalize_kana: bool = False, honorific_folding: bool = False,
    variant_matching: bool = False, stem_matching: bool = False,
    compound_matching: bool = False,
) -> Tuple[Set[str], Set[str], Set[Tuple[str, str]], Set[Tuple[str, str]], Set[Tuple[str, str]], Set[Tuple[str, str]], Set[Tuple[str, str]], Set[Tuple[str, str]]]:
    """Parse one day's raw term_meta entries into the presence sets ``SeenWindow`` holds.

    Mirrors the entry parsing of ``dictionary_manager._build_index_from_raw`` (the ``count > 0``
    gate, the ``㋕`` kana-occurrence marker attributing the entry to its reading, kana
    normalization), but records presence rather than accumulating counts. Base presence reduces
    to the expression set, so there is no ``(expr, reading)`` map.

    ``phrase_entries``, ``suffix_phrase_entries``, ``suru_entries`` and ``negative_entries`` are each a sliver of any
    dict, so they are retained unconditionally. ``variant_entries`` (every kanji-bearing entry)
    and ``stem_entries`` (every entry whose reading differs from its written form) are most of a
    dict, so they are gated on their flags. ``stem_entries`` serves both ``stem_matching`` and
    ``compound_matching``, so either flag collects it. Retaining ``variant_entries``
    unconditionally cost ~70% on this function."""
    exprs: Set[str] = set()
    phrase_entries: Set[Tuple[str, str]] = set()
    suffix_phrase_entries: Set[Tuple[str, str]] = set()
    suru_entries: Set[Tuple[str, str]] = set()
    variant_entries: Set[Tuple[str, str]] = set()
    stem_entries: Set[Tuple[str, str]] = set()
    negative_entries: Set[Tuple[str, str]] = set()
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
            # Kana-only entries are attributed to the reading, so a combine_word_forms lookup
            # is what then credits kanji-bearing cards carrying that reading.
            effective = reading if (is_kana_occurrences and reading) else expression
            if normalize_kana:
                effective = to_hiragana(effective)
                if reading:
                    reading = to_hiragana(reading)
            exprs.add(effective)
            if dm._is_phrase_entry(effective, reading):
                phrase_entries.add((effective, reading))
            if dm._is_suffix_phrase_entry(effective, reading):
                suffix_phrase_entries.add((effective, reading))
            if dm._is_suru_entry(effective, reading):
                suru_entries.add((effective, reading))
            if dm._is_negative_entry(effective, reading):
                negative_entries.add((effective, reading))
            if variant_matching and dm._is_variant_entry(effective, reading):
                variant_entries.add((effective, reading))
            # Gate mirrors what _stem_candidates can ever probe for. A candidate always carries a
            # reading distinct from its written form, so entries where the two are equal can never
            # be hit and are not worth retaining. It is also the counting side's kana-pair gate,
            # which is what makes _stem_compound_present exact rather than merely close.
            if (stem_matching or compound_matching) and reading and reading != effective:
                stem_entries.add((effective, reading))

    honorific_stripped: Set[str] = set()
    if honorific_folding:
        for expr in exprs:
            if not expr.startswith(dm._HONORIFIC_PREFIXES):
                continue
            # Every entry in _HONORIFIC_PREFIXES is a single character.
            stripped = expr[1:]
            if dm._honorific_fold_allowed(stripped, exprs):
                honorific_stripped.add(stripped)
    return (exprs, honorific_stripped, phrase_entries, suffix_phrase_entries, suru_entries,
            variant_entries, stem_entries, negative_entries)


# How many presence sets build_seen_day returns, and how many BUILD-time flags key the caches
# (normalize_kana, honorific_folding, variant_matching, stem_matching, compound_matching). Named
# rather than inlined because get_seen_window slices its cache keys positionally to prune stale
# windows. Hard-coding the width there is what made adding a flag a silent breakage.
_SEEN_SET_COUNT = 8
_BUILD_FLAG_COUNT = 5

# (folder_name, mtime, + the five build flags) -> the presence sets build_seen_day returns.
# mtime self-invalidates on current-day rewrites, and the build flags are in the key so a config
# change rebuilds.
#
# prefix_matching, suffix_matching and combine_word_forms are query-time flags applied in
# SeenWindow.contains, deliberately NOT part of the build key: the phrase-entry sets are a sliver
# of any dict, so they are retained unconditionally. variant_matching, stem_matching and
# compound_matching ARE build flags, because their entry sets span most of the dict and building
# them unconditionally would tax every seen lookup for features that are off by default.
_day_cache: Dict[Tuple, Tuple[Set[str], Set[str], Set[Tuple[str, str]], Set[Tuple[str, str]],
                              Set[Tuple[str, str]], Set[Tuple[str, str]], Set[Tuple[str, str]],
                              Set[Tuple[str, str]]]] = {}


def _seen_dict_name(folder: str) -> str:
    """``dictionary_manager`` dict-name for one seen date folder, a nested path under user_files
    that ``_dict_dir`` and ``_load_term_meta_raw`` resolve transparently."""
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


def _seen_day_for(folder, mtime, normalize_kana, honorific_folding, variant_matching=False,
                  stem_matching=False, compound_matching=False):
    """Cached presence sets (see ``build_seen_day``) for one date folder at a known
    ``mtime``. Rebuilt when the mtime or the build-time flags change. A missing folder/file
    yields empty sets ("nothing seen that day"). Splitting the mtime out lets ``get_seen_window``
    stat each day once and reuse it for both the build and the cache key."""
    key = (folder, mtime, normalize_kana, honorific_folding, variant_matching, stem_matching,
           compound_matching)
    cached = _day_cache.get(key)
    if cached is not None:
        return cached

    # Drop any stale entry for this folder (old mtime / flag combo) so the current day's
    # repeated rewrites don't leak one cache entry per save.
    for stale in [k for k in _day_cache if k[0] == folder]:
        del _day_cache[stale]

    data = dm._load_term_meta_raw(_seen_dict_name(folder))
    sets = (build_seen_day(data, normalize_kana, honorific_folding, variant_matching,
                           stem_matching, compound_matching)
            if data is not None else tuple(set() for _ in range(_SEEN_SET_COUNT)))
    _day_cache[key] = sets
    return sets


def _merge_seen_days(days) -> "SeenWindow":
    """Union the per-day ``(exprs, honorific_stripped, phrase_entries, suffix_phrase_entries,
    suru_entries, variant_entries, stem_entries, negative_entries)`` sets into one ``SeenWindow``. Presence is idempotent across days, so a plain
    union replaces the old additive count merge (and the per-day separation it needed for the
    non-additive reading-mismatch fallback)."""
    exprs: Set[str] = set()
    honorific: Set[str] = set()
    phrases: Set[Tuple[str, str]] = set()
    suffix_phrases: Set[Tuple[str, str]] = set()
    surus: Set[Tuple[str, str]] = set()
    variants: Set[Tuple[str, str]] = set()
    stems: Set[Tuple[str, str]] = set()
    negatives: Set[Tuple[str, str]] = set()
    for de, dh, dp, dsp, dsu, dv, dst, dn in days:
        exprs |= de
        honorific |= dh
        phrases |= dp
        suffix_phrases |= dsp
        surus |= dsu
        variants |= dv
        stems |= dst
        negatives |= dn
    return SeenWindow(exprs, honorific, phrases, suffix_phrases, surus, variants, stems, negatives)


# Merged-window cache: (per-day (folder, mtime) + build flags) -> SeenWindow. Keyed on mtimes so
# it self-invalidates, and pruned by folder set so a window is REPLACED rather than accumulated
# when a day's file is rewritten.
#
# Pruning is what keeps this bounded. Today's seen dict is rewritten repeatedly while immersing,
# and each rewrite yields a new signature, so without pruning every stale window stayed resident
# with its own copies of the union sets (~6.7 MB for a seen:30 window with variants and its lazy
# views), letting the cap alone allow several hundred MB of dead windows. The cap remains only as
# a backstop for configs with many distinct seen:N values.
_WINDOW_CACHE_CAP = 64
_window_cache: Dict[Tuple, "SeenWindow"] = {}


def clear_cache() -> None:
    """Drop the per-day and merged-window caches. For tests only; at runtime the mtime keying
    self-invalidates."""
    _day_cache.clear()
    _window_cache.clear()


# A search evaluates the same window over thousands of notes, so the window is resolved ONCE by
# get_seen_window before the note loop, then looked up per note by SeenWindow.contains, purely
# in memory. Presence makes the window a single merged membership set, with no per-day separation
# and no additive sum, so each note is one set lookup plus a binary search when prefix matching.

def get_seen_window(
    n: int,
    normalize_kana: bool = False,
    honorific_folding: bool = False,
    variant_matching: bool = False,
    stem_matching: bool = False,
    compound_matching: bool = False,
    today: Optional[date] = None,
) -> "SeenWindow":
    """Resolve the last ``n`` days (rollover-aware) to a single ``SeenWindow``. Stats each day's
    file once; the window is cached by its (per-day folder+mtime, build flags) signature.
    ``prefix_matching`` and ``suffix_matching`` are not parameters, because the union sets are
    identical with or without them and the sorted views are built lazily on the returned object.
    ``variant_matching``, ``stem_matching`` and ``compound_matching`` ARE parameters: their entry
    sets span most of the dict, so they are only collected when asked for (see
    ``build_seen_day``). ``today`` injectable
    for tests."""
    if n <= 0:
        return SeenWindow(*(set() for _ in range(_SEEN_SET_COUNT)))
    if today is None:
        today = today_date()
    folders = [date_to_folder(d) for d in window_dates(today, n)]
    mtimes = [_source_mtime(f) for f in folders]  # one stat per day
    sig = tuple(zip(folders, mtimes)) + (normalize_kana, honorific_folding, variant_matching,
                                        stem_matching, compound_matching)
    cached = _window_cache.get(sig)
    if cached is not None:
        return cached
    days = [_seen_day_for(f, m, normalize_kana, honorific_folding, variant_matching,
                          stem_matching, compound_matching)
            for f, m in zip(folders, mtimes)]
    window = _merge_seen_days(days)

    # Drop any window built over these same days and build flags at an older set of mtimes: a
    # rewrite of today's dict must REPLACE its windows, not stack a fresh one beside every
    # previous version. Mirrors the stale-entry prune in _seen_day_for.
    identity = (tuple(folders),) + sig[-_BUILD_FLAG_COUNT:]
    for stale in [k for k in _window_cache
                  if (tuple(f for f, _m in k[:-_BUILD_FLAG_COUNT]),)
                  + k[-_BUILD_FLAG_COUNT:] == identity]:
        del _window_cache[stale]

    if len(_window_cache) >= _WINDOW_CACHE_CAP:
        _window_cache.pop(next(iter(_window_cache)))
    _window_cache[sig] = window
    return window


def window_mtimes(n: int, today: Optional[date] = None) -> Tuple:
    """The source mtimes of the last ``n`` daily dicts, a cheap fingerprint that changes whenever
    any day in the window is rewritten (notably today's dict during immersion). Invalidates
    full-scan result memos that ``mw.col.mod`` cannot see, since the seen files change outside
    the collection."""
    if n <= 0:
        return ()
    if today is None:
        today = today_date()
    return tuple(_source_mtime(date_to_folder(d)) for d in window_dates(today, n))
