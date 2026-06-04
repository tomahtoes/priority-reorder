import re
from aqt import mw
from collections import Counter
from typing import Set, Dict, List
from .config_manager import Config

_kanji_manager_instance = None

def get_kanji_manager(config: Config) -> 'KanjiManager':
    global _kanji_manager_instance
    if _kanji_manager_instance is None:
        _kanji_manager_instance = KanjiManager(config)
    else:
        prev_field = _kanji_manager_instance.config.search_config.expression_field
        new_field = config.search_config.expression_field
        _kanji_manager_instance.config = config
        if prev_field != new_field:
            _kanji_manager_instance.initialized = False
            _kanji_manager_instance.known_kanji_counts.clear()
            _kanji_manager_instance._scan_sig = None
            _kanji_manager_instance._mod_seen = None
    return _kanji_manager_instance

class KanjiManager:
    """Manages known Kanji stats."""
    def __init__(self, config: Config) -> None:
        self.config = config
        self.known_kanji_counts: Counter = Counter()
        self.initialized = False
        # Signature of the "known" card set (count, sum of card mtimes). The known
        # set is rebuilt whenever this changes; see _known_signature.
        self._scan_sig = None
        # Last seen mw.col.mod. Cheap gate so the signature query runs at most once
        # per collection change, not once per get_unknown_kanji_count call.
        self._mod_seen = None
        self._kanji_pattern = re.compile(r'[\u4e00-\u9faf]')

    def _extract_kanji(self, text: str) -> List[str]:
        return self._kanji_pattern.findall(text)

    def initialize(self) -> None:
        # A "known" kanji comes from a word that is graduated and not suspended.
        mod = mw.col.mod
        if self.initialized and mod == self._mod_seen:
            return
        self._mod_seen = mod

        sig = self._known_signature()
        if self.initialized and sig == self._scan_sig:
            return

        self.known_kanji_counts.clear()
        self._scan_all()
        self._scan_sig = sig
        self.initialized = True

    def _relevant_field(self):
        """(expression_field, [model ids containing it]) for the configured field,
        or (None, []) when unset. Field index is resolved per model in _scan_all."""
        expression_field = self.config.search_config.expression_field
        if not expression_field:
            return None, []
        mids = [
            model['id']
            for model in mw.col.models.all()
            if expression_field in mw.col.models.field_map(model)
        ]
        return expression_field, mids

    def _known_signature(self):
        """Cheap fingerprint of the graduated-and-not-suspended card set: (count,
        sum of card mtimes). Insensitive to repositioning (which only touches the
        `due` of new cards), sensitive to study/suspend/unsuspend of the set."""
        try:
            _, mids = self._relevant_field()
            if not mids:
                return (0, 0)
            mids_csv = ",".join(str(m) for m in mids)
            row = mw.col.db.first(
                f"select count(*), coalesce(sum(c.mod), 0) from cards c "
                f"join notes n on n.id = c.nid "
                f"where n.mid in ({mids_csv}) and c.type in (2, 3) and c.queue != -1"
            )
            return tuple(row) if row else (0, 0)
        except Exception as e:
            import traceback
            print(f"[priority-reorder] kanji signature failed: {e}")
            traceback.print_exc()
            return None  # never matches stored sig -> force a rebuild

    def _scan_all(self) -> None:
        expression_field = self.config.search_config.expression_field
        if not expression_field:
            return

        try:
            for model in mw.col.models.all():
                flds_map = mw.col.models.field_map(model)
                if expression_field in flds_map:
                    idx = flds_map[expression_field][0]
                    mid = model['id']

                    rows = mw.col.db.list(f"select n.flds from notes n join cards c on c.nid = n.id where n.mid = {mid} and c.type in (2, 3) and c.queue != -1 group by n.id")

                    for flds_str in rows:
                        fields = flds_str.split('\x1f')
                        if idx < len(fields):
                            self.known_kanji_counts.update(self._extract_kanji(fields[idx]))
        except Exception as e:
            import traceback
            print(f"[priority-reorder] kanji scan failed: {e}")
            traceback.print_exc()

    def get_unknown_kanji_count(self, text: str) -> int:
        self.initialize()
        return sum(1 for char in self._extract_kanji(text) if self.known_kanji_counts[char] == 0)

    def get_kanji_count(self, text: str) -> int:
        return len(self._extract_kanji(text))
