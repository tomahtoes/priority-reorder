from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class NoteData:
    """Represents the data of a note relevant for reordering."""
    note_id: int
    expression: str = ""
    reading: str = ""
    sort_field_value: float = float("inf")
    # False when the sort field was empty / non-numeric / <= 0. Such cards have
    # no usable ordering data and are always placed last (see _sort_cards).
    has_sort_value: bool = False

@dataclass
class Card:
    """Represents a card to be reordered."""
    card_id: int
    note_id: int
    data: NoteData
