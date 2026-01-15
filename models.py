from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class NoteData:
    """Represents the data of a note relevant for reordering."""
    note_id: int
    expression: str = ""
    reading: str = ""
    sort_field_value: float = float("inf")

@dataclass
class Card:
    """Represents a card to be reordered."""
    card_id: int
    note_id: int
    data: NoteData
