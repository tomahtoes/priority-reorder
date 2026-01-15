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
        _kanji_manager_instance.config = config
    return _kanji_manager_instance

class KanjiManager:
    """Manages known Kanji stats."""
    def __init__(self, config: Config) -> None:
        self.config = config
        self.known_kanji_counts: Counter = Counter()
        self.initialized = False
        self._kanji_pattern = re.compile(r'[\u4e00-\u9faf]')

    def _extract_kanji(self, text: str) -> List[str]:
        return self._kanji_pattern.findall(text)

    def initialize(self) -> None:
        if self.initialized:
            self._incremental_update()
            return

        self._scan_all()
        self.initialized = True
        
    def _scan_all(self) -> None:
        expression_field = self.config.search_config.expression_field
        if not expression_field:
            return

        try:
            models = mw.col.models.all()
            for model in models:
                flds_map = mw.col.models.field_map(model)
                if expression_field in flds_map:
                    idx = flds_map[expression_field][0]
                    mid = model['id']
                    
                    rows = mw.col.db.list(f"select n.flds from notes n join cards c on c.nid = n.id where n.mid = {mid} and c.queue != 0 group by n.id")
                    
                    for flds_str in rows:
                        fields = flds_str.split('\x1f')
                        if idx < len(fields):
                            self.known_kanji_counts.update(self._extract_kanji(fields[idx]))
        except Exception:
            pass

    def _incremental_update(self) -> None:
        expression_field = self.config.search_config.expression_field
        if not expression_field:
            return

        try:
            query = f'introduced:3 "{expression_field}:_*"'
            note_ids = mw.col.find_notes(query)
            
            if not note_ids:
                return

            for nid in note_ids:
                try:
                    note = mw.col.get_note(nid)
                    if expression_field in note:
                        self.known_kanji_counts.update(self._extract_kanji(note[expression_field]))
                except Exception:
                    continue
        except Exception:
            pass

    def get_unknown_kanji_count(self, text: str) -> int:
        if not self.initialized:
            self.initialize()
        return sum(1 for char in self._extract_kanji(text) if self.known_kanji_counts[char] == 0)

    def get_kanji_count(self, text: str) -> int:
        return len(self._extract_kanji(text))
