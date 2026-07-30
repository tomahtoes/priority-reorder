from typing import Optional


class NoteData:
    """Represents the data of a note relevant for reordering.

    Deliberately a plain __slots__ class rather than a dataclass: one instance is retained per
    note in the cross-run cache, so on a 100k-note collection the per-instance __dict__ costs
    real memory for nothing (measured ~8 MB across NoteData + Card). `@dataclass(slots=True)`
    would express this more neatly but needs Python 3.10, and the rest of the addon stays
    3.9-compatible (no match statements, no `X | None` annotations)."""

    __slots__ = ("note_id", "expression", "reading", "sort_field_value", "has_sort_value")

    def __init__(
        self,
        note_id: int,
        expression: str = "",
        reading: str = "",
        sort_field_value: float = float("inf"),
        # False when the sort field was empty / non-numeric / <= 0. Such cards have
        # no usable ordering data and are always placed last (see _sort_cards).
        has_sort_value: bool = False,
    ) -> None:
        self.note_id = note_id
        self.expression = expression
        self.reading = reading
        self.sort_field_value = sort_field_value
        self.has_sort_value = has_sort_value

    def __repr__(self) -> str:
        return (
            f"NoteData(note_id={self.note_id!r}, expression={self.expression!r}, "
            f"reading={self.reading!r}, sort_field_value={self.sort_field_value!r}, "
            f"has_sort_value={self.has_sort_value!r})"
        )


class Card:
    """Represents a card to be reordered. __slots__ for the same reason as NoteData."""

    __slots__ = ("card_id", "note_id", "data")

    def __init__(self, card_id: int, note_id: int, data: Optional[NoteData] = None) -> None:
        self.card_id = card_id
        self.note_id = note_id
        self.data = data

    def __repr__(self) -> str:
        return f"Card(card_id={self.card_id!r}, note_id={self.note_id!r}, data={self.data!r})"
