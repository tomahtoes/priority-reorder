from typing import Dict, List, Optional, Tuple
from aqt import mw
from anki.utils import ids2str
from .models import Card, NoteData
from .config_manager import Config
from .utils import parse_sort_value

class DataManager:
    """Manages loading and caching of Card and Note data."""
    def __init__(self, config: Config) -> None:
        self.config = config
        self._note_cache: Dict[int, NoteData] = {}
        self._card_cache: Dict[int, Card] = {}
        # mid -> (expression_idx, reading_idx, sort_idx); None when a field is
        # absent from that note type.
        self._field_idx_cache: Dict[int, Tuple[Optional[int], Optional[int], Optional[int]]] = {}

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

    def get_note_data(self, note_id: int) -> Optional[NoteData]:
        if note_id in self._note_cache:
            return self._note_cache[note_id]

        note = mw.col.get_note(note_id)

        expression_field = self.config.search_config.expression_field
        reading_field = self.config.search_config.expression_reading_field
        sort_field = self.config.sort_field

        expression = note[expression_field] if expression_field in note else ""
        reading = note[reading_field] if reading_field in note else ""
        sort_val_str = note[sort_field] if sort_field in note else ""
        sort_val, has_sort = parse_sort_value(sort_val_str)

        note_data = NoteData(
            note_id=note_id,
            expression=expression,
            reading=reading,
            sort_field_value=sort_val,
            has_sort_value=has_sort,
        )
        self._note_cache[note_id] = note_data
        return note_data

    def get_cards_from_search(self, search_string: str) -> List[Card]:
        if search_string.strip():
            final_search = f"{search_string} is:new"
        else:
            final_search = "is:new"

        try:
            card_ids = mw.col.find_cards(final_search)
        except Exception as e:
            import traceback
            print(f"[priority-reorder] find_cards failed for search {final_search!r}: {e}")
            traceback.print_exc()
            return []

        self._bulk_load(card_ids)
        return [c for cid in card_ids if (c := self._card_cache.get(cid)) is not None]

    def clear_cache(self) -> None:
        self._note_cache.clear()
        self._card_cache.clear()
        self._field_idx_cache.clear()
