import time
from typing import Dict, List, NamedTuple, Optional, Tuple
from aqt import mw
from anki.utils import ids2str

try:  # inside Anki: isolated package namespace
    from .models import Card, NoteData
    from .config_manager import Config
    from .utils import parse_sort_value, parse_comparator, to_hiragana
    from .search import (
        has_custom_term,
        parse_custom_terms,
        _strip_custom_terms,
        _candidate_restriction_allowed,
    )
    from .dictionary_manager import expand_dict_names, occurrence_counter, _kanji_skeleton
    from .kanji_manager import get_kanji_manager
    from .kanji_readings import (
        reading_slots, unresolved_count,
        UNRESOLVED_WARN_RATE, UNRESOLVED_MIN_SAMPLE,
    )
    from . import kanji_readings
    from . import seen_manager
except ImportError:  # pytest / flat-import context
    from models import Card, NoteData
    from config_manager import Config
    from utils import parse_sort_value, parse_comparator, to_hiragana
    from search import (
        has_custom_term,
        parse_custom_terms,
        _strip_custom_terms,
        _candidate_restriction_allowed,
    )
    from dictionary_manager import expand_dict_names, occurrence_counter, _kanji_skeleton
    from kanji_manager import get_kanji_manager
    from kanji_readings import (
        reading_slots, unresolved_count,
        UNRESOLVED_WARN_RATE, UNRESOLVED_MIN_SAMPLE,
    )
    import kanji_readings
    import seen_manager

class SearchResult(NamedTuple):
    """Cards matched by a search, plus the standard-query match count from before
    any custom occurrences:/f/kanji: post-filtering (equal when there is none)."""
    cards: List[Card]
    raw_count: int

# Ids are inlined via ids2str, so SQLite's bound-parameter limit never applies;
# the only real bound is statement length (1 MB default), and 5000 ids x ~14
# bytes is ~70 KB — a 100k-card backlog is 20 round-trips instead of 112.
_BULK_CHUNK_SIZE = 5000

# Cross-run cache of parsed note data, nid -> (notes.mod, NoteData). Note fields
# rarely change between reorders, so warm runs only fetch field text for notes
# whose mod stamp moved. Invalidated per note via mod, and wholesale when the
# fingerprint (configured field names + notetype layout) changes or the profile
# switches (see clear_note_cache).
_note_data_cache: Dict[int, Tuple[int, NoteData]] = {}
_note_data_cache_fp = None

# Relative per-card cost of each custom term, used to order the post-filter passes in
# _get_cards_filtered. `freq`/`length` read an already-loaded attribute; `kanji` walks the
# expression against a counter; `seen` is set membership plus a binary search; `occ` sums
# the dictionary rules. Measured on a 120k-entry dictionary at 0.07 / 0.10 / 4.9 / 10.4
# microseconds per card for freq / length / seen / occ with every matching flag on.
_TERM_COST = {"length": 0, "freq": 1, "kanji": 2, "seen": 3, "occ": 4}


def clear_note_cache() -> None:
    """Drop the cross-run note cache. Wired to profile_did_open — note ids from
    one profile must never serve another — and used by tests. The reading-slot
    memo is keyed on field text rather than note id, but it is dropped here too
    so a profile switch cannot leave one profile's working set resident."""
    global _note_data_cache_fp
    _note_data_cache.clear()
    _note_data_cache_fp = None
    kanji_readings.clear_cache()


def _notetypes_mod_sum() -> int:
    """Cheap fingerprint of notetype layout. Field renames/reorders bump the
    notetype's mod but not every note's mod, so cached NoteData built with the
    old field indices must be invalidated through this."""
    try:
        return int(mw.col.db.scalar("select coalesce(sum(mod), 0) from notetypes") or 0)
    except Exception:
        try:
            return sum(int(m.get("mod", 0)) for m in mw.col.models.all())
        except Exception:
            return -1

class DataManager:
    """Manages loading and caching of Card and Note data."""
    def __init__(self, config: Config) -> None:
        self.config = config
        self._card_cache: Dict[int, Card] = {}
        # mid -> (expression_idx, reading_idx, sort_idx); None when a field is
        # absent from that note type.
        self._field_idx_cache: Dict[int, Tuple[Optional[int], Optional[int], Optional[int]]] = {}
        # Per-run caches shared across every search in a single reorder: the same
        # standard query / custom predicate recurs across many priority searches.
        self._search_cache: Dict[str, List[Card]] = {}                # find_cards by query
        # Nested one level so the per-card probe is an int-keyed lookup: the outer key is
        # fixed when the predicate is built, so hoisting it there keeps the inner loop from
        # allocating and hashing a tuple per card.
        self._occ_count_cache: Dict[Tuple[str, ...], Dict[int, int]] = {}   # dicts -> nid -> count
        self._kanji_count_cache: Dict[Tuple[str, int], Dict[int, int]] = {}  # (type, target) -> nid -> count
        self._seen_contains_cache: Dict[int, Dict[int, bool]] = {}           # n -> nid -> contained
        # nid -> (folded expression, folded reading, kanji skeleton). Kana folding and skeleton
        # derivation are per-note constants, but every predicate and every seen window used to
        # redo them; a card checked by `occurrences:` plus seen:1/7/30 paid four folds.
        self._note_derived_cache: Dict[int, Tuple[str, str, Optional[str]]] = {}
        self._kanji_manager = None  # lazy
        # Distinct `seen:N` levels across the whole config, and their windows resolved together
        # against one reference date — see _seen_windows.
        self._seen_levels: Optional[List[int]] = None
        self._seen_window_map: Optional[Dict[int, "seen_manager.SeenWindow"]] = None
        self._note_fp_checked = False  # cross-run cache validated once per run
        # Sub-stage wall-clock accumulators (ms), merged into the reorder timings
        # line. NOT disjoint stages: `load` accumulates across both the
        # find_matches and load_cards top-level stages, and `kanji_scan` is the
        # rescan slice of `kanji_init`.
        self.stage_ms: Dict[str, float] = {}
        # Reading slots resolved / left unexplained across the cards this run
        # actually evaluated, counted once per note (on a cache miss), not once
        # per comparison. Surfaced as the new_reading diagnostic: a misconfigured
        # reading field wildcards everything, which turns kanji:new_reading into
        # "matches every card" without raising anything.
        self._nr_total = 0
        self._nr_unresolved = 0

    def _add_ms(self, key: str, t0: float) -> None:
        self.stage_ms[key] = self.stage_ms.get(key, 0.0) + (time.perf_counter() - t0) * 1000

    def _resolve_field_indices(self, mid: int) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        cached = self._field_idx_cache.get(mid)
        if cached is not None:
            return cached

        model = mw.col.models.get(mid)
        if not model:
            result: Tuple[Optional[int], Optional[int], Optional[int]] = (None, None, None)
        else:
            fmap = mw.col.models.field_map(model)  # name -> (ord, field_dict)

            def idx(name: str) -> Optional[int]:
                entry = fmap.get(name)
                return entry[0] if entry else None

            result = (
                idx(self.config.search_config.expression_field),
                idx(self.config.search_config.expression_reading_field),
                idx(self.config.sort_field),
            )
        self._field_idx_cache[mid] = result
        return result

    def _check_note_cache_fp(self) -> None:
        """Validate the cross-run note cache once per run: any change to the
        configured field names or the notetype layout (field renames/reorders
        don't bump notes.mod) drops the whole cache."""
        global _note_data_cache_fp
        if self._note_fp_checked:
            return
        self._note_fp_checked = True
        fp = (
            self.config.search_config.expression_field,
            self.config.search_config.expression_reading_field,
            self.config.sort_field,
            _notetypes_mod_sum(),
        )
        if fp != _note_data_cache_fp:
            _note_data_cache.clear()
            _note_data_cache_fp = fp

    def _bulk_load(self, card_ids: List[int]) -> None:
        """Load every not-yet-cached card in bulk SQL passes instead of one
        backend round-trip per card. Two phases: (1) card->note linkage plus each
        note's mod stamp — no field text; (2) field text for only the notes the
        cross-run cache doesn't already hold at that mod. On warm runs phase 2
        shrinks to just the notes edited since the previous reorder."""
        missing = [cid for cid in card_ids if cid not in self._card_cache]
        if not missing:
            return

        self._check_note_cache_fp()
        t0 = time.perf_counter()
        try:
            links = []
            try:
                for start in range(0, len(missing), _BULK_CHUNK_SIZE):
                    chunk = missing[start:start + _BULK_CHUNK_SIZE]
                    links.extend(mw.col.db.all(
                        "select c.id, c.nid, n.mid, n.mod from cards c "
                        f"join notes n on n.id = c.nid where c.id in {ids2str(chunk)}"
                    ))
            except Exception as e:
                import traceback
                print(f"[priority-reorder] bulk card load failed: {e}")
                traceback.print_exc()
                return

            stale = list({
                nid for _cid, nid, _mid, nmod in links
                if (entry := _note_data_cache.get(nid)) is None or entry[0] != nmod
            })
            if stale:
                rows = []
                try:
                    for start in range(0, len(stale), _BULK_CHUNK_SIZE):
                        chunk = stale[start:start + _BULK_CHUNK_SIZE]
                        rows.extend(mw.col.db.all(
                            f"select id, mid, mod, flds from notes where id in {ids2str(chunk)}"
                        ))
                except Exception as e:
                    import traceback
                    print(f"[priority-reorder] bulk note load failed: {e}")
                    traceback.print_exc()
                    return

                for nid, mid, nmod, flds in rows:
                    expr_i, read_i, sort_i = self._resolve_field_indices(mid)
                    fields = flds.split("\x1f")
                    nf = len(fields)
                    sort_val, has_sort = parse_sort_value(
                        fields[sort_i] if sort_i is not None and sort_i < nf else ""
                    )
                    _note_data_cache[nid] = (nmod, NoteData(
                        note_id=nid,
                        expression=fields[expr_i] if expr_i is not None and expr_i < nf else "",
                        reading=fields[read_i] if read_i is not None and read_i < nf else "",
                        sort_field_value=sort_val,
                        has_sort_value=has_sort,
                    ))

            for cid, nid, _mid, _nmod in links:
                entry = _note_data_cache.get(nid)
                if entry is not None:
                    self._card_cache[cid] = Card(card_id=cid, note_id=nid, data=entry[1])
        finally:
            self._add_ms("load", t0)

    def get_card(self, card_id: int) -> Optional[Card]:
        if card_id in self._card_cache:
            return self._card_cache[card_id]

        # Fallback single-card path (most callers go through the bulk loader).
        self._bulk_load([card_id])
        return self._card_cache.get(card_id)

    def get_cards(self, card_ids) -> Dict[int, Card]:
        """Load a batch of cards in one bulk pass and return the found ones as a
        map. Ids whose card vanished between find_cards and here are dropped."""
        ids = list(card_ids)
        self._bulk_load(ids)
        return {cid: c for cid in ids if (c := self._card_cache.get(cid)) is not None}

    def get_cards_from_search(self, search_string: str) -> SearchResult:
        raw = search_string.strip()

        # Fast path: a conjunctive query carrying custom occurrences:/f/kanji: terms.
        # Resolve the standard part ONCE per distinct query (shared across the many
        # priority searches that reuse the same deck/filter) and apply the custom
        # predicates in Python over the already-loaded note data — no per-search
        # full collection scan and no per-search re-run of the standard query.
        if raw and has_custom_term(raw):
            stripped = " ".join(_strip_custom_terms(raw).split())
            if _candidate_restriction_allowed(raw, stripped):
                return self._get_cards_filtered(raw, stripped)

        # Default path: no custom terms, or a disjunctive/grouped query whose custom
        # terms must be resolved by the patched find_cards (correctness over speed).
        # The user part is parenthesized because Anki binds AND tighter than OR:
        # bare `deck:A or deck:B is:new` would scope is:new to the last branch only.
        cards = self._cards_for_search(f"({raw}) is:new" if raw else "is:new")
        return SearchResult(cards, len(cards))

    def _cards_for_search(self, final_search: str) -> List[Card]:
        """find_cards(final_search) -> loaded Cards, memoized by query string for the
        duration of the run (the collection is read-only until repositioning).

        The finished Card list is what's memoized, not the id list: a config with several
        priority searches over the same deck hits this repeatedly, and re-resolving ids to
        Cards each time cost two more passes over the whole backlog per hit.

        Returns the SHARED list — callers must treat it as read-only. Both do: _find_matches
        only reads card ids out of it, and _get_cards_filtered rebinds its local to a new
        filtered list rather than mutating in place."""
        cards = self._search_cache.get(final_search)
        if cards is not None:
            return cards

        t0 = time.perf_counter()
        try:
            card_ids = list(mw.col.find_cards(final_search))
        except Exception as e:
            import traceback
            print(f"[priority-reorder] find_cards failed for search {final_search!r}: {e}")
            traceback.print_exc()
            return []
        finally:
            self._add_ms("fc", t0)

        self._bulk_load(card_ids)
        cards = [c for cid in card_ids if (c := self._card_cache.get(cid)) is not None]
        self._search_cache[final_search] = cards
        return cards

    def _get_cards_filtered(self, raw_query: str, stripped: str) -> SearchResult:
        base = " ".join(t for t in stripped.split() if t != "-")  # drop stray '-' from negation
        cards = self._cards_for_search(f"({base}) is:new" if base else "is:new")
        raw_count = len(cards)

        # The terms are a pure conjunction of independent predicates, so any evaluation
        # order yields the same set in the same order — but parse_custom_terms emits by
        # kind, which happens to be close to most-expensive-first. Cheapest first shrinks
        # the list before the dictionary and seen lookups run over it (measured ~150x
        # between an `f` comparison and an all-flags `occurrences:` lookup), and it can
        # never cost more work overall: the per-note memos are shared across every search,
        # so any note whose expensive value is still needed computes it exactly once.
        for kind, args, negated in sorted(parse_custom_terms(raw_query),
                                          key=lambda t: _TERM_COST.get(t[0], 9)):
            # Before _term_predicate, not after: building the kanji and seen predicates
            # scans the collection / stats and parses the daily dicts, which an empty
            # candidate list must never pay for.
            if not cards:
                break
            pred = self._term_predicate(kind, args)
            t0 = time.perf_counter()
            cards = [c for c in cards if (not pred(c)) == negated]
            self._add_ms(f"filter_{kind}", t0)
        return SearchResult(cards, raw_count)

    def _term_predicate(self, kind: str, args):
        if kind == "freq":
            op, thresh = args
            comparator = parse_comparator(op)
            return lambda c: comparator(c.data.sort_field_value, thresh)

        if kind == "length":
            op, thresh = args
            comparator = parse_comparator(op)
            # No empty-expression skip: an empty field counts as length 0
            # (mirrors resolve_length).
            return lambda c: comparator(len(c.data.expression), thresh)

        if kind == "occ":
            dict_str, op, thresh = args
            comparator = parse_comparator(op)
            dict_names = expand_dict_names(dict_str)
            cfg = self.config
            # Index resolution and flag dispatch happen once here rather than per card —
            # they used to be ~79% of the warm multi-dict lookup. `prefolded` because
            # _note_derived already kana-folded both strings for the whole run.
            count_occurrences = occurrence_counter(
                dict_names,
                normalize_kana=cfg.kana_normalization,
                combine_word_forms=cfg.combine_word_forms,
                prefix_matching=cfg.prefix_matching,
                suffix_matching=cfg.suffix_matching,
                variant_matching=cfg.variant_matching,
                stem_matching=cfg.stem_matching,
                honorific_folding=cfg.honorific_folding,
                prefolded=True,
            )
            cache = self._occ_count_cache.setdefault(tuple(dict_names), {})
            derived = self._note_derived

            def occ_pred(c: Card) -> bool:
                data = c.data
                if not data.expression or not data.reading:
                    return False
                nid = c.note_id
                value = cache.get(nid)
                if value is None:
                    # derived(c) is (folded expression, folded reading, kanji skeleton) —
                    # exactly the counter's signature.
                    value = count_occurrences(*derived(c))
                    cache[nid] = value
                return comparator(value, thresh)

            return occ_pred

        if kind == "kanji":
            check_type, target, op, thresh = args
            comparator = parse_comparator(op)
            km = self._km()
            if check_type == "new_reading":
                # Before initialize(): the reading index is built during the scan,
                # and enabling it afterwards would force a second one.
                km.enable_readings()
            t0 = time.perf_counter()
            km.initialize()  # once per predicate build, not per evaluated card
            self._add_ms("kanji_init", t0)
            # getattr: tests inject bare fakes without the timing attribute.
            scan_ms = getattr(km, "last_scan_ms", None)
            if scan_ms:
                self.stage_ms["kanji_scan"] = self.stage_ms.get("kanji_scan", 0.0) + scan_ms
            cache = self._kanji_count_cache.setdefault((check_type, target), {})

            if check_type == "new_reading":
                # Raw fields, not _note_derived: that helper's kana folding is
                # gated on the kana_normalization flag, which belongs to the
                # occurrence dictionaries. reading_slots folds internally and
                # memoises per (expression, reading) anyway.
                def kanji_pred(c: Card) -> bool:
                    data = c.data
                    if not data.expression or not data.reading:
                        return False
                    nid = c.note_id
                    value = cache.get(nid)
                    if value is None:
                        slots = reading_slots(data.expression, data.reading)
                        counts = km.known_reading_counts
                        value = sum(1 for slot in slots if counts[slot] < target)
                        cache[nid] = value
                        self._nr_total += len(slots)
                        self._nr_unresolved += unresolved_count(slots)
                    return comparator(value, thresh)

                return kanji_pred

            count_kanji = (
                (lambda text: km.get_unknown_kanji_count(text, target))
                if check_type == "new" else km.get_kanji_count
            )

            def kanji_pred(c: Card) -> bool:
                expression = c.data.expression
                if not expression:
                    return False
                nid = c.note_id
                value = cache.get(nid)
                if value is None:
                    value = count_kanji(expression)
                    cache[nid] = value
                return comparator(value, thresh)

            return kanji_pred

        if kind == "seen":
            (n,) = args
            if n <= 0:
                return lambda c: False  # seen:0 matches nothing (mirrors resolve_seen)
            cfg = self.config
            # Hoisted locals: read once per predicate build, not per card.
            normalize_kana = cfg.kana_normalization
            combine_word_forms = cfg.combine_word_forms
            prefix_matching = cfg.prefix_matching
            suffix_matching = cfg.suffix_matching
            variant_matching = cfg.variant_matching
            stem_matching = cfg.stem_matching
            honorific_folding = cfg.honorific_folding
            # Resolve every level's window ONCE per run (one filesystem stat per day), so the
            # per-card check is a pure in-memory membership lookup.
            t0 = time.perf_counter()
            windows = self._seen_windows(n, normalize_kana, honorific_folding,
                                         variant_matching, stem_matching)
            self._add_ms("seen_win", t0)
            levels = self._seen_levels
            top = levels[-1]
            # Memoized per level, then per note id — the flags are fixed for the run, so
            # they stay out of the key (mirrors _occ_count_cache). If today's
            # seen file is rewritten mid-run, a later predicate build can see a
            # newer window while the memo keeps the earlier answers — accepted,
            # like every other per-run cache here.
            by_level = {
                level: self._seen_contains_cache.setdefault(level, {}) for level in levels
            }
            own = by_level[n]
            top_cache = by_level[top]
            derived = self._note_derived
            query_flags = dict(
                normalize_kana=normalize_kana,
                combine_word_forms=combine_word_forms,
                prefix_matching=prefix_matching,
                suffix_matching=suffix_matching,
                variant_matching=variant_matching,
                stem_matching=stem_matching,
                honorific_folding=honorific_folding,
                prefolded=True,
            )

            def seen_pred(c: Card) -> bool:
                if not c.data.expression:
                    return False
                nid = c.note_id
                value = own.get(nid)
                if value is not None:
                    return value
                expression, reading, card_kanji = derived(c)

                # Evaluate the LARGEST level first. The windows nest (seen:1 ⊆ seen:7 ⊆
                # seen:30 — window_dates(today, n) is the last n days from one shared
                # reference date) and every branch of contains() is a membership test over
                # union sets, so it is monotone: a miss at the top is a miss at every level
                # and settles them all in one probe. A hit says nothing about the smaller
                # windows, so those are still evaluated — but in a new-card backlog misses
                # are the overwhelming majority, which is where the three-windows-for-the-
                # price-of-one saving comes from.
                top_seen = top_cache.get(nid)
                if top_seen is None:
                    top_seen = windows[top].contains(
                        expression, reading, card_kanji=card_kanji, **query_flags
                    )
                    top_cache[nid] = top_seen
                if not top_seen:
                    for level_cache in by_level.values():
                        level_cache[nid] = False
                    return False
                if n == top:
                    return True

                value = windows[n].contains(
                    expression, reading, card_kanji=card_kanji, **query_flags
                )
                own[nid] = value
                # Monotone in both directions: seen within n days => seen within any
                # longer window, and NOT seen within n days => not seen within any
                # shorter one. Filling both sides settles every configured level from
                # the two probes above.
                for level, level_cache in by_level.items():
                    if value:
                        if level >= n:
                            level_cache[nid] = True
                    elif level <= n:
                        level_cache[nid] = False
                return value

            return seen_pred

        return lambda c: False

    def reading_diagnostics(self) -> Dict[str, str]:
        """One-line summaries of how much of the collection the reading table
        could explain, for the reorder timings line and the summary window.

        Empty unless a new_reading term actually ran. Two views: the cards this
        run evaluated, and the learned collection behind the index — the latter
        is the more useful of the two, being computed once over everything."""
        out: Dict[str, str] = {}
        rate = None
        if self._nr_total:
            rate = self._nr_unresolved / self._nr_total
            out["new_reading_cards"] = "%.0f%% unresolved of %d kanji" % (
                100.0 * rate, self._nr_total)
        km = self._kanji_manager
        known_rate = km.unresolved_reading_rate() if km is not None else None
        if known_rate is not None:
            out["new_reading_known"] = "%.0f%% unresolved" % (100.0 * known_rate)
        # A separate key rather than a threshold the UI re-derives: the values
        # above are formatted for people, and parsing a percentage back out of
        # them to decide whether to alarm would be one copy of the rule too many.
        if (rate is not None
                and self._nr_total >= UNRESOLVED_MIN_SAMPLE
                and rate >= UNRESOLVED_WARN_RATE):
            out["new_reading_warning"] = (
                "%.0f%% of the kanji checked by kanji:new_reading have a reading "
                "the table cannot explain (usually under 25%%). Check that "
                "search_fields.expression_reading_field names the field holding "
                "the kana reading." % (100.0 * rate)
            )
        return out

    def _note_derived(self, card: Card) -> Tuple[str, str, Optional[str]]:
        """``(expression, reading, kanji skeleton)`` for a note, computed once per run.

        Kana folding and skeleton derivation depend only on the note and on run-fixed config,
        yet both the occurrence path and every seen window used to redo them per card. The
        skeleton is derived from the FOLDED expression, which is what both consumers expect
        (``to_hiragana`` leaves CJK ideographs untouched, so the two agree either way), and is
        left None when variant matching is off — nothing reads it then."""
        nid = card.note_id
        value = self._note_derived_cache.get(nid)
        if value is None:
            expression = card.data.expression
            reading = card.data.reading
            if self.config.kana_normalization:
                expression = to_hiragana(expression)
                reading = to_hiragana(reading)
            skeleton = _kanji_skeleton(expression) if self.config.variant_matching else None
            value = (expression, reading, skeleton)
            self._note_derived_cache[nid] = value
        return value

    def _seen_level_set(self, n: int) -> List[int]:
        """Every distinct positive ``seen:N`` in the configured searches, ascending.

        Scanned from the config rather than accumulated as predicates are built, because the
        short-circuit needs the largest level up front — the first search to carry a `seen:`
        term must already know whether a bigger window exists elsewhere in the config."""
        if self._seen_levels is None:
            queries: List[str] = []
            raw = self.config.priority_search
            if isinstance(raw, str):
                queries.append(raw)
            elif raw:
                queries.extend(q for q in raw if isinstance(q, str))
            if isinstance(self.config.normal_search, str):
                queries.append(self.config.normal_search)

            levels = set()
            for query in queries:
                if not query:
                    continue
                try:
                    for term_kind, term_args, _negated in parse_custom_terms(query):
                        if term_kind == "seen" and term_args[0] > 0:
                            levels.add(term_args[0])
                except Exception:  # a malformed search must not break the reorder
                    continue
            levels.add(n)  # the level being built, even if the scan somehow missed it
            self._seen_levels = sorted(levels)
        elif n not in self._seen_levels:  # defensive: a caller outside the configured searches
            self._seen_levels = sorted(set(self._seen_levels) | {n})
            self._seen_window_map = None  # force a re-resolve that includes the new level
        return self._seen_levels

    def _seen_windows(self, n: int, normalize_kana: bool, honorific_folding: bool,
                      variant_matching: bool, stem_matching: bool
                      ) -> Dict[int, "seen_manager.SeenWindow"]:
        """All configured levels' windows, resolved against ONE reference date.

        Sharing `today` across levels is what makes them nest: resolved independently, two
        windows straddling the rollover hour could disagree about which day is 'today' and the
        short-circuit's monotonicity assumption would not hold."""
        levels = self._seen_level_set(n)
        if self._seen_window_map is None:
            today = seen_manager.today_date()
            self._seen_window_map = {
                level: seen_manager.get_seen_window(
                    level, normalize_kana, honorific_folding, variant_matching,
                    stem_matching, today=today
                )
                for level in levels
            }
        return self._seen_window_map

    def _km(self):
        if self._kanji_manager is None:
            self._kanji_manager = get_kanji_manager(self.config)
        return self._kanji_manager
