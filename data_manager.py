from typing import Dict, List, Set, Optional
from aqt import mw
from .models import Card, NoteData
from .config_manager import Config

class DataManager:
    """Manages loading and caching of Card and Note data."""
    def __init__(self, config: Config) -> None:
        self.config = config
        self._note_cache: Dict[int, NoteData] = {}
        self._card_cache: Dict[int, Card] = {}

    def get_card(self, card_id: int) -> Optional[Card]:
        if card_id in self._card_cache:
            return self._card_cache[card_id]
        
        try:
            anki_card = mw.col.get_card(card_id)
            note_id = anki_card.nid
            
            note_data = self.get_note_data(note_id)
            if not note_data:
                return None
                
            card = Card(card_id=card_id, note_id=note_id, data=note_data)
            self._card_cache[card_id] = card
            return card
        except Exception:
            return None

    def get_note_data(self, note_id: int) -> Optional[NoteData]:
        if note_id in self._note_cache:
            return self._note_cache[note_id]
        
        try:
            note = mw.col.get_note(note_id)
            
            expression_field = self.config.search_config.expression_field
            reading_field = self.config.search_config.expression_reading_field
            sort_field = self.config.sort_field
            
            expression = note[expression_field] if expression_field in note else ""
            reading = note[reading_field] if reading_field in note else ""
            
            sort_val_str = note[sort_field] if sort_field in note else ""
            sort_val = float("inf")
            if sort_val_str:
                try:
                    val = float(sort_val_str)
                    if val > 0:
                        sort_val = val
                except ValueError:
                    pass

            note_data = NoteData(
                note_id=note_id,
                expression=expression,
                reading=reading,
                sort_field_value=sort_val
            )
            self._note_cache[note_id] = note_data
            return note_data
        except Exception:
            return None

    def get_cards_from_search(self, search_string: str) -> List[Card]:
        if search_string.strip():
            final_search = f"{search_string} is:new"
        else:
            final_search = "is:new"
            
        try:
            card_ids = mw.col.find_cards(final_search)
            return [self.get_card(cid) for cid in card_ids if self.get_card(cid)]
        except Exception:
            return []
    
    def clear_cache(self) -> None:
        self._note_cache.clear()
        self._card_cache.clear()
