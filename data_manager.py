import time
from typing import Dict, List, NamedTuple, Optional, Tuple
from aqt import mw
from anki.utils import ids2str

try:  # inside Anki: isolated package namespace
    from .models import Card, NoteData
    from .config_manager import Config
    from .utils import parse_sort_value, parse_comparator
    from .search import (
        has_custom_term,
        parse_custom_terms,
        _strip_custom_terms,
        _candidate_restriction_allowed,
    )
    from .dictionary_manager import expand_dict_names, occurrence_count
    from .kanji_manager import get_kanji_manager
    from . import seen_manager
except ImportError:  # pytest / flat-import context
    from models import Card, NoteData
    from config_manager import Config
    from utils import parse_sort_value, parse_comparator
    from search import (
        has_custom_term,
        parse_custom_terms,
        _strip_custom_terms,
        _candidate_restriction_allowed,
    )
    from dictionary_manager import expand_dict_names, occurrence_count
    from kanji_manager import get_kanji_manager
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


def clear_note_cache() -> None:
    """Drop the cross-run note cache. Wired to profile_did_open — note ids from
    one profile must never serve another — and used by tests."""
    global _note_data_cache_fp
    _note_data_cache.clear()
    _note_data_cache_fp = None


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
        self._search_cache: Dict[str, List[int]] = {}                 # find_cards by query
        self._occ_count_cache: Dict[Tuple[Tuple[str, ...], int], int] = {}  # (dicts, nid) -> count
        self._kanji_count_cache: Dict[Tuple[str, int, int], int] = {}  # (check_type, target, nid) -> count
        self._seen_contains_cache: Dict[Tuple[int, int], bool] = {}   # (n, nid) -> contained
        self._kanji_manager = None  # lazy
        self._note_fp_checked = False  # cross-run cache validated once per run
        # Sub-stage wall-clock accumulators (ms), merged into the reorder timings
        # line. NOT disjoint stages: `load` accumulates across both the
        # find_matches and load_cards top-level stages, and `kanji_scan` is the
        # rescan slice of `kanji_init`.
        self.stage_ms: Dict[str, float] = {}

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
        duration of the run (the collection is read-only until repositioning)."""
        card_ids = self._search_cache.get(final_search)
        if card_ids is None:
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
            self._search_cache[final_search] = card_ids

        self._bulk_load(card_ids)
        return [c for cid in card_ids if (c := self._card_cache.get(cid)) is not None]

    def _get_cards_filtered(self, raw_query: str, stripped: str) -> SearchResult:
        base = " ".join(t for t in stripped.split() if t != "-")  # drop stray '-' from negation
        cards = self._cards_for_search(f"({base}) is:new" if base else "is:new")
        raw_count = len(cards)

        for kind, args, negated in parse_custom_terms(raw_query):
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
            dkey = tuple(dict_names)

            def occ_pred(c: Card) -> bool:
                if not c.data.expression or not c.data.reading:
                    return False
                return comparator(self._occ_count(dkey, dict_names, c), thresh)

            return occ_pred

        if kind == "kanji":
            check_type, target, op, thresh = args
            comparator = parse_comparator(op)
            km = self._km()
            t0 = time.perf_counter()
            km.initialize()  # once per predicate build, not per evaluated card
            self._add_ms("kanji_init", t0)
            # getattr: tests inject bare fakes without the timing attribute.
            scan_ms = getattr(km, "last_scan_ms", None)
            if scan_ms:
                self.stage_ms["kanji_scan"] = self.stage_ms.get("kanji_scan", 0.0) + scan_ms

            def kanji_pred(c: Card) -> bool:
                if not c.data.expression:
                    return False
                return comparator(self._kanji_count(check_type, target, c, km), thresh)

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
            honorific_folding = cfg.honorific_folding
            # Resolve the window ONCE per predicate build (one filesystem stat per
            # day), so the per-card check is a pure in-memory membership lookup.
            t0 = time.perf_counter()
            window = seen_manager.get_seen_window(
                n, normalize_kana, honorific_folding
            )
            self._add_ms("seen_win", t0)
            # Memoized per (n, note id) — the flags are fixed for the run, so
            # they stay out of the key (mirrors _occ_count_cache). If today's
            # seen file is rewritten mid-run, a later predicate build can see a
            # newer window while the memo keeps the earlier answers — accepted,
            # like every other per-run cache here.
            cache = self._seen_contains_cache
            contains = window.contains

            def seen_pred(c: Card) -> bool:
                if not c.data.expression:
                    return False
                key = (n, c.note_id)
                value = cache.get(key)
                if value is None:
                    value = contains(
                        c.data.expression,
                        c.data.reading,
                        normalize_kana=normalize_kana,
                        combine_word_forms=combine_word_forms,
                        prefix_matching=prefix_matching,
                        suffix_matching=suffix_matching,
                        honorific_folding=honorific_folding,
                    )
                    cache[key] = value
                return value

            return seen_pred

        return lambda c: False

    def _occ_count(self, dkey: Tuple[str, ...], dict_names: List[str], card: Card) -> int:
        key = (dkey, card.note_id)
        value = self._occ_count_cache.get(key)
        if value is None:
            value = occurrence_count(
                dict_names,
                card.data.expression,
                card.data.reading,
                normalize_kana=self.config.kana_normalization,
                combine_word_forms=self.config.combine_word_forms,
                prefix_matching=self.config.prefix_matching,
                suffix_matching=self.config.suffix_matching,
                honorific_folding=self.config.honorific_folding,
            )
            self._occ_count_cache[key] = value
        return value

    def _kanji_count(self, check_type: str, target: int, card: Card, km) -> int:
        key = (check_type, target, card.note_id)
        value = self._kanji_count_cache.get(key)
        if value is None:
            if check_type == "new":
                value = km.get_unknown_kanji_count(card.data.expression, target)
            else:  # "num"
                value = km.get_kanji_count(card.data.expression)
            self._kanji_count_cache[key] = value
        return value

    def _km(self):
        if self._kanji_manager is None:
            self._kanji_manager = get_kanji_manager(self.config)
        return self._kanji_manager
