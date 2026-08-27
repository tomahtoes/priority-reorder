import time
from aqt import mw
from anki.utils import ids2str
from collections import Counter
from typing import Dict, List, Optional, Tuple

try:  # inside Anki: isolated package namespace
    from .config_manager import Config
    from .kanji_readings import reading_slots, unresolved_count
    from .utils import KANJI_RE
except ImportError:  # pytest / flat-import context
    from config_manager import Config
    from kanji_readings import reading_slots, unresolved_count
    from utils import KANJI_RE

_kanji_manager_instance = None

def get_kanji_manager(config: Config) -> 'KanjiManager':
    global _kanji_manager_instance
    if _kanji_manager_instance is None:
        _kanji_manager_instance = KanjiManager(config)
    else:
        # Both fields matter: the reading field feeds known_reading_counts, so a
        # rename there must invalidate the cached slots exactly like an
        # expression-field rename invalidates the kanji counts.
        prev = _kanji_manager_instance._field_names()
        _kanji_manager_instance.config = config
        if prev != _kanji_manager_instance._field_names():
            _kanji_manager_instance.initialized = False
            _kanji_manager_instance.known_kanji_counts.clear()
            _kanji_manager_instance.known_reading_counts.clear()
            _kanji_manager_instance._note_kanji.clear()
            _kanji_manager_instance._scan_sig = None
            _kanji_manager_instance._mod_seen = None
            _kanji_manager_instance._reading_total = 0
            _kanji_manager_instance._reading_unresolved = 0
    return _kanji_manager_instance

class KanjiManager:
    """Manages known Kanji stats."""
    def __init__(self, config: Config) -> None:
        self.config = config
        self.known_kanji_counts: Counter = Counter()
        # Slot key -> number of learned words using that reading of that kanji.
        # Same shape as known_kanji_counts, which is what makes the bracketed
        # [T] target work identically: a reading stays new until T learned words
        # use it. Only populated once a new_reading term asks (see
        # enable_readings) -- users of kanji:new/kanji:num pay nothing.
        self.known_reading_counts: Counter = Counter()
        self._reading_mode = False
        # Running totals over the known set, for the unresolved-rate diagnostic.
        # Maintained incrementally in _credit_note rather than recomputed, which
        # would mean walking every learned note on each reorder.
        self._reading_total = 0
        self._reading_unresolved = 0
        self.initialized = False
        # Signature of the "known" card set (count, sum of card mtimes). The known
        # set is re-synced whenever this changes; see _known_signature.
        self._scan_sig = None
        # Last seen mw.col.mod. Cheap gate so the signature query runs at most once
        # per collection change, not once per get_unknown_kanji_count call.
        self._mod_seen = None
        # Per-note snapshot backing the incremental sync: nid -> (notes.mod,
        # kanji credited to the counter, reading slots credited). Lets a changed
        # known set be diffed instead of fully rescanned. The slot list is empty
        # unless _reading_mode is on.
        self._note_kanji: Dict[int, Tuple[int, List[str], Tuple[str, ...]]] = {}
        # Wall-clock ms of the last initialize() that actually rebuilt or synced;
        # None when the call was a no-op. Read by the reorder timings.
        self.last_scan_ms: Optional[float] = None

    def _field_names(self) -> Tuple[str, str]:
        sc = self.config.search_config
        return sc.expression_field, sc.expression_reading_field

    def enable_readings(self) -> None:
        """Start tracking per-kanji reading slots.

        Flipping this invalidates the snapshot on purpose: it was built without
        slots, so the next initialize() has to rebuild rather than diff."""
        if self._reading_mode:
            return
        self._reading_mode = True
        self.initialized = False
        self._scan_sig = None
        self._mod_seen = None
        self.known_reading_counts.clear()
        self._note_kanji.clear()
        self._reading_total = 0
        self._reading_unresolved = 0

    def unresolved_reading_rate(self) -> Optional[float]:
        """Fraction of the learned collection's kanji whose reading the table
        could not explain, or None when nothing has been counted.

        A reading field holding the wrong data (markup, the wrong field, empty)
        makes every word unresolved, which silently turns kanji:new_reading into
        "matches everything" without raising. This is how that becomes visible."""
        if not self._reading_total:
            return None
        return self._reading_unresolved / self._reading_total

    def _extract_kanji(self, text: str) -> List[str]:
        # utils.KANJI_RE, not a local pattern: this used to be a narrower
        # `[一-龯]`, so kanji:num/kanji:new silently ignored Ext A, the
        # compatibility ideographs (﨑/塚) and all of Ext B — characters utils.is_kanji,
        # and therefore variant matching, counts.
        return KANJI_RE.findall(text)

    def initialize(self) -> None:
        # A "known" kanji comes from a word that is graduated and not suspended.
        self.last_scan_ms = None
        mod = mw.col.mod
        if self.initialized and mod == self._mod_seen:
            return
        self._mod_seen = mod

        sig = self._known_signature()
        # `sig is not None` matters: a failed signature query returns None, and storing
        # that below would otherwise make the NEXT failure compare None == None and skip
        # the rescan it was supposed to force.
        if self.initialized and sig is not None and sig == self._scan_sig:
            return

        t0 = time.perf_counter()
        try:
            if self.initialized and self._note_kanji:
                try:
                    self._sync_known_notes()
                except Exception as e:
                    import traceback
                    print(f"[priority-reorder] incremental kanji sync failed: {e}")
                    traceback.print_exc()
                    self._rebuild_all()
            else:
                self._rebuild_all()
        except Exception as e:
            # A failed rebuild leaves a partial counter. Don't stamp it as current —
            # clearing both gates makes the next call retry instead of serving half the
            # known set for the rest of the session. `initialized` is still set, because
            # get_unknown_kanji_count's safety net would otherwise re-enter this per card.
            # The reorder continues on best-effort counts rather than aborting outright.
            import traceback
            print(f"[priority-reorder] kanji scan failed; counts may be incomplete: {e}")
            traceback.print_exc()
            self._scan_sig = None
            self._mod_seen = None
            self.initialized = True
            self.last_scan_ms = (time.perf_counter() - t0) * 1000
            return
        self.last_scan_ms = (time.perf_counter() - t0) * 1000
        self._scan_sig = sig
        self.initialized = True

    def _expression_field_indices(self) -> Tuple[Optional[str], Dict[int, Tuple[int, Optional[int]]]]:
        """(expression_field, {mid: (expression ord, reading ord or None)}) for
        the note types carrying the configured expression field, or (None, {})
        when unset.

        Membership still keys off the expression field alone: a note type
        without the reading field is not excluded, it simply contributes no
        reading slots. That mirrors how occurrences: treats such notes, and
        keeps kanji:new/kanji:num matching exactly what they matched before."""
        expression_field = self.config.search_config.expression_field
        if not expression_field:
            return None, {}
        reading_field = self.config.search_config.expression_reading_field
        idx_by_mid = {}
        for model in mw.col.models.all():
            fmap = mw.col.models.field_map(model)
            if expression_field in fmap:
                read = fmap[reading_field][0] if reading_field in fmap else None
                idx_by_mid[model['id']] = (fmap[expression_field][0], read)
        return expression_field, idx_by_mid

    def _known_signature(self):
        """Cheap fingerprint of the graduated-and-not-suspended card set: (count,
        sum of card mtimes). Insensitive to repositioning (which only touches the
        `due` of new cards), sensitive to study/suspend/unsuspend of the set."""
        try:
            _, idx_by_mid = self._expression_field_indices()
            if not idx_by_mid:
                return (0, 0)
            mids_csv = ",".join(str(m) for m in idx_by_mid)
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

    def _credit_note(self, nid: int, nmod: int, flds_str: str,
                     idx: Tuple[int, Optional[int]]) -> None:
        """(Re)credit one note's kanji -- and, in reading mode, its reading slots
        -- to the counters, replacing any previous contribution recorded in the
        snapshot."""
        expr_idx, read_idx = idx
        old = self._note_kanji.pop(nid, None)
        if old is not None:
            self.known_kanji_counts.subtract(old[1])
            if old[2]:
                self.known_reading_counts.subtract(old[2])
                self._reading_total -= len(old[2])
                self._reading_unresolved -= unresolved_count(old[2])
        fields = flds_str.split('\x1f')
        expression = fields[expr_idx] if expr_idx < len(fields) else ""
        kanji = self._extract_kanji(expression)
        if kanji:
            self.known_kanji_counts.update(kanji)
        slots: Tuple[str, ...] = ()
        # Only in reading mode, so the kanji:new path stays exactly as cheap as
        # it was; and only when the note type actually carries the reading field.
        if self._reading_mode and kanji and read_idx is not None and read_idx < len(fields):
            slots = reading_slots(expression, fields[read_idx])
            if slots:
                self.known_reading_counts.update(slots)
                self._reading_total += len(slots)
                self._reading_unresolved += unresolved_count(slots)
        self._note_kanji[nid] = (nmod, kanji, slots)

    def _rebuild_all(self) -> None:
        self.known_kanji_counts.clear()
        self.known_reading_counts.clear()
        self._note_kanji.clear()
        self._reading_total = 0
        self._reading_unresolved = 0
        self._scan_all()

    def _scan_all(self) -> None:
        """Credit every known note's kanji. Raises on a failed scan rather than leaving a
        half-built counter behind: initialize() would otherwise stamp the partial result as
        authoritative and never rescan."""
        expression_field, idx_by_mid = self._expression_field_indices()
        if not expression_field:
            return

        try:
            for mid, idx in idx_by_mid.items():
                rows = mw.col.db.all(f"select n.id, n.mod, n.flds from notes n join cards c on c.nid = n.id where n.mid = {mid} and c.type in (2, 3) and c.queue != -1 group by n.id")

                for nid, nmod, flds_str in rows:
                    self._credit_note(nid, nmod, flds_str, idx)
        except Exception as e:
            import traceback
            print(f"[priority-reorder] kanji scan failed: {e}")
            traceback.print_exc()
            raise

    def _sync_known_notes(self) -> None:
        """Diff the known-note set against the per-note snapshot: subtract notes
        that left the set, fetch field text only for notes that are new or
        edited. The membership query transfers no field text, so the reorder
        after a study session re-reads a handful of notes instead of the whole
        learned collection."""
        expression_field, idx_by_mid = self._expression_field_indices()
        if not expression_field or not idx_by_mid:
            # No note type carries the field -> the known set is empty.
            self.known_kanji_counts.clear()
            self.known_reading_counts.clear()
            self._note_kanji.clear()
            self._reading_total = 0
            self._reading_unresolved = 0
            return

        mids_csv = ",".join(str(m) for m in idx_by_mid)
        current: Dict[int, int] = dict(mw.col.db.all(
            f"select n.id, n.mod from notes n join cards c on c.nid = n.id "
            f"where n.mid in ({mids_csv}) and c.type in (2, 3) and c.queue != -1 "
            f"group by n.id"
        ))

        for nid in [nid for nid in self._note_kanji if nid not in current]:
            _, kanji, slots = self._note_kanji.pop(nid)
            self.known_kanji_counts.subtract(kanji)
            if slots:
                self.known_reading_counts.subtract(slots)
                self._reading_total -= len(slots)
                self._reading_unresolved -= unresolved_count(slots)

        stale = [
            nid for nid, nmod in current.items()
            if (entry := self._note_kanji.get(nid)) is None or entry[0] != nmod
        ]
        for start in range(0, len(stale), 5000):
            chunk = stale[start:start + 5000]
            for nid, mid, flds_str in mw.col.db.all(
                f"select id, mid, flds from notes where id in {ids2str(chunk)}"
            ):
                idx = idx_by_mid.get(mid)
                if idx is None:
                    continue
                self._credit_note(nid, current[nid], flds_str, idx)

        # Lookups treat 0 like a missing key, but pruning keeps the counters tidy
        # after subtractions.
        for k in [k for k, v in self.known_kanji_counts.items() if v <= 0]:
            del self.known_kanji_counts[k]
        for k in [k for k, v in self.known_reading_counts.items() if v <= 0]:
            del self.known_reading_counts[k]

    def get_unknown_kanji_count(self, text: str, target: int = 1) -> int:
        # A kanji counts as unknown ("new") until `target` learned words contain
        # it; the default of 1 is the classic "no learned word has it".
        # Safety net only: callers evaluating many notes invoke initialize() once
        # per batch (the collection can't change mid-batch), so the per-call
        # mw.col.mod read this used to do is skipped on the hot path.
        if not self.initialized:
            self.initialize()
        return sum(1 for char in self._extract_kanji(text) if self.known_kanji_counts[char] < target)

    def get_new_reading_count(self, expression: str, reading: str, target: int = 1) -> int:
        """How many kanji in `expression` are used here in a reading that fewer
        than `target` learned words have taught.

        The mirror of get_unknown_kanji_count, one level finer: that one asks
        whether the kanji has been seen at all, this one whether *this reading*
        of it has. A kanji the table cannot explain here (jukujikun, ateji, a
        gikun reading) counts as new by definition -- its slot carries the kana
        span, so learning the word credits it and it stops firing."""
        if not self.initialized:
            self.initialize()
        counts = self.known_reading_counts
        return sum(1 for slot in reading_slots(expression, reading) if counts[slot] < target)

    def get_kanji_count(self, text: str) -> int:
        return len(self._extract_kanji(text))
