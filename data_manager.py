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

class SearchResult(NamedTuple):
    """Cards matched by a search, plus the standard-query match count from before
    any custom occurrences:/f/kanji: post-filtering (equal when there is none)."""
    cards: List[Card]
    raw_count: int

class DataManager:
    """Manages loading and caching of Card and Note data."""
    def __init__(self, config: Config) -> None:
        self.config = config
        self._note_cache: Dict[int, NoteData] = {}
        self._card_cache: Dict[int, Card] = {}
        # mid -> (expression_idx, reading_idx, sort_idx); None when a field is
        # absent from that note type.
        self._field_idx_cache: Dict[int, Tuple[Optional[int], Optional[int], Optional[int]]] = {}
        # Per-run caches shared across every search in a single reorder: the same
        # standard query / custom predicate recurs across many priority searches.
        self._search_cache: Dict[str, List[int]] = {}                 # find_cards by query
        self._occ_count_cache: Dict[Tuple[Tuple[str, ...], int], int] = {}  # (dicts, nid) -> count
        self._kanji_count_cache: Dict[Tuple[str, int], int] = {}      # (check_type, nid) -> count
        self._kanji_manager = None  # lazy

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

    def _bulk_load(self, card_ids: List[int]) -> None:
        """Load every not-yet-cached card in a single SQL pass instead of one
        backend round-trip per card. Field indices are resolved once per note
        type."""
        missing = [cid for cid in card_ids if cid not in self._card_cache]
        if not missing:
            return

        try:
            rows = mw.col.db.all(
                "select c.id, c.nid, n.mid, n.flds from cards c "
                f"join notes n on n.id = c.nid where c.id in {ids2str(missing)}"
            )
        except Exception as e:
            import traceback
            print(f"[priority-reorder] bulk card load failed: {e}")
            traceback.print_exc()
            return

        for cid, nid, mid, flds in rows:
            expr_i, read_i, sort_i = self._resolve_field_indices(mid)
            note_data = self._note_cache.get(nid)
            if note_data is None:
                fields = flds.split("\x1f")

                def field_at(i: Optional[int]) -> str:
                    return fields[i] if i is not None and i < len(fields) else ""

                sort_val, has_sort = parse_sort_value(field_at(sort_i))
                note_data = NoteData(
                    note_id=nid,
                    expression=field_at(expr_i),
                    reading=field_at(read_i),
                    sort_field_value=sort_val,
                    has_sort_value=has_sort,
                )
                self._note_cache[nid] = note_data
            self._card_cache[cid] = Card(card_id=cid, note_id=nid, data=note_data)

    def get_card(self, card_id: int) -> Optional[Card]:
        if card_id in self._card_cache:
            return self._card_cache[card_id]

        # Fallback single-card path (most callers go through the bulk loader).
        self._bulk_load([card_id])
        return self._card_cache.get(card_id)

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
            try:
                card_ids = list(mw.col.find_cards(final_search))
            except Exception as e:
                import traceback
                print(f"[priority-reorder] find_cards failed for search {final_search!r}: {e}")
                traceback.print_exc()
                return []
            self._search_cache[final_search] = card_ids

        self._bulk_load(card_ids)
        return [c for cid in card_ids if (c := self._card_cache.get(cid)) is not None]

    def _get_cards_filtered(self, raw_query: str, stripped: str) -> SearchResult:
        base = " ".join(t for t in stripped.split() if t != "-")  # drop stray '-' from negation
        cards = self._cards_for_search(f"({base}) is:new" if base else "is:new")
        raw_count = len(cards)

        for kind, args, negated in parse_custom_terms(raw_query):
            pred = self._term_predicate(kind, args)
            cards = [c for c in cards if (not pred(c)) == negated]
        return SearchResult(cards, raw_count)

    def _term_predicate(self, kind: str, args):
        if kind == "freq":
            op, thresh = args
            comparator = parse_comparator(op)
            return lambda c: comparator(c.data.sort_field_value, thresh)

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
            check_type, op, thresh = args
            comparator = parse_comparator(op)
            km = self._km()
            km.initialize()  # once per predicate build, not per evaluated card

            def kanji_pred(c: Card) -> bool:
                if not c.data.expression:
                    return False
                return comparator(self._kanji_count(check_type, c, km), thresh)

            return kanji_pred

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
                honorific_folding=self.config.honorific_folding,
            )
            self._occ_count_cache[key] = value
        return value

    def _kanji_count(self, check_type: str, card: Card, km) -> int:
        key = (check_type, card.note_id)
        value = self._kanji_count_cache.get(key)
        if value is None:
            if check_type == "new":
                value = km.get_unknown_kanji_count(card.data.expression)
            else:  # "num"
                value = km.get_kanji_count(card.data.expression)
            self._kanji_count_cache[key] = value
        return value

    def _km(self):
        if self._kanji_manager is None:
            self._kanji_manager = get_kanji_manager(self.config)
        return self._kanji_manager
