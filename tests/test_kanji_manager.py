"""Unit tests for KanjiManager: kanji extraction/counting, the signature-gated
known-kanji scan, and the singleton's reset-on-field-change behavior."""

import types
from collections import Counter

import pytest

import kanji_manager as kmod
from config_manager import Config, SearchConfig
from kanji_manager import KanjiManager, get_kanji_manager


class _FakeModels:
    def __init__(self, fields=("Expression",)):
        self._fields = {name: i for i, name in enumerate(fields)}

    def all(self):
        return [{"id": 1, "name": "Basic", "_fields": dict(self._fields)}]

    def field_map(self, model):
        return {name: (ord_, {"name": name}) for name, ord_ in model["_fields"].items()}


class _FakeDB:
    """Serves the signature query (first) and the three scan/sync query shapes
    (all): the full scan (n.flds), the incremental membership pass (n.mod, no
    field text), and the stale-note field fetch (id in (...))."""

    def __init__(self, sig=(1, 100), notes=()):
        self.sig = sig            # returned by the signature query (db.first)
        self.notes = list(notes)  # (nid, nmod, flds) rows, all mid=1
        self.first_calls = 0
        self.scan_calls = 0       # full-scan queries (transfer field text)
        self.member_calls = 0     # membership queries (no field text)
        self.fetch_calls = 0      # stale-note field fetches
        self.fetch_ids = []       # nids requested by those fetches

    def first(self, sql):
        self.first_calls += 1
        return self.sig

    def all(self, sql):
        if "n.flds" in sql:
            self.scan_calls += 1
            return [(nid, nmod, flds) for nid, nmod, flds in self.notes]
        if "n.mod" in sql:
            self.member_calls += 1
            return [(nid, nmod) for nid, nmod, _ in self.notes]
        self.fetch_calls += 1
        inside = sql[sql.rindex("(") + 1 : sql.rindex(")")]
        ids = {int(x) for x in inside.split(",") if x.strip()}
        self.fetch_ids.extend(sorted(ids))
        return [(nid, 1, flds) for nid, nmod, flds in self.notes if nid in ids]


@pytest.fixture
def fake_col(monkeypatch):
    def build(mod=1, sig=(1, 100), notes=()):
        col = types.SimpleNamespace(mod=mod, models=_FakeModels(), db=_FakeDB(sig, notes))
        monkeypatch.setattr(kmod.mw, "col", col, raising=False)
        return col

    return build


@pytest.fixture(autouse=True)
def _reset_singleton():
    kmod._kanji_manager_instance = None
    yield
    kmod._kanji_manager_instance = None


# --- counting ----------------------------------------------------------------

def test_get_kanji_count_counts_cjk_chars_only():
    km = KanjiManager(Config())
    assert km.get_kanji_count("彫刻abcの12") == 2
    assert km.get_kanji_count("ひらがなカナ") == 0


def test_get_unknown_kanji_count_against_known_set():
    km = KanjiManager(Config())
    km.initialized = True  # skip the collection scan
    km.known_kanji_counts = Counter({"彫": 1})
    assert km.get_unknown_kanji_count("彫刻") == 1  # 刻 is unknown
    assert km.get_unknown_kanji_count("彫") == 0


def test_get_unknown_kanji_count_with_target():
    # A kanji counts as "new" until `target` learned words contain it.
    km = KanjiManager(Config())
    km.initialized = True  # skip the collection scan
    km.known_kanji_counts = Counter({"彫": 3, "刻": 1})
    assert km.get_unknown_kanji_count("彫刻") == 0       # default target 1 == old behavior
    assert km.get_unknown_kanji_count("彫刻", 2) == 1    # 刻 has only 1 learned word
    assert km.get_unknown_kanji_count("彫刻", 4) == 2    # both below 4
    assert km.get_unknown_kanji_count("刻刻", 2) == 2    # positions counted, not distinct kanji
    assert km.get_unknown_kanji_count("彫刻", 0) == 0    # degenerate: count < 0 never true


# --- initialize() gating -------------------------------------------------------

def test_initialize_builds_known_counts_from_graduated_notes(fake_col):
    fake_col(notes=[(1, 1, "彫刻\x1fちょうこく"), (2, 1, "刻\x1fこく")])
    km = KanjiManager(Config())
    km.initialize()
    assert km.known_kanji_counts == Counter({"彫": 1, "刻": 2})


def test_initialize_skips_everything_when_mod_unchanged(fake_col):
    col = fake_col(notes=[(1, 1, "語\x1f")])
    km = KanjiManager(Config())
    km.initialize()
    km.initialize()
    assert col.db.first_calls == 1  # signature queried once
    assert col.db.scan_calls == 1   # scanned once


def test_initialize_syncs_only_when_known_signature_changes(fake_col):
    col = fake_col(notes=[(1, 1, "語\x1f")])
    km = KanjiManager(Config())
    km.initialize()

    col.mod = 2  # collection changed, but the graduated set's signature did not
    km.initialize()
    assert col.db.first_calls == 2
    assert col.db.scan_calls == 1   # no rescan
    assert col.db.member_calls == 0

    col.mod = 3
    col.db.sig = (2, 200)  # the known set actually changed
    km.initialize()
    assert col.db.scan_calls == 1   # still no full rescan...
    assert col.db.member_calls == 1  # ...just the incremental membership pass
    assert col.db.fetch_calls == 0   # nothing new or edited -> no field text
    assert km.known_kanji_counts == Counter({"語": 1})


def test_incremental_sync_credits_new_notes_and_debits_departed(fake_col):
    col = fake_col(notes=[(1, 1, "彫刻\x1fちょうこく"), (2, 1, "刻\x1fこく")])
    km = KanjiManager(Config())
    km.initialize()
    assert km.known_kanji_counts == Counter({"彫": 1, "刻": 2})

    # Note 2 leaves the known set (suspended/forgotten), note 3 graduates.
    col.mod = 2
    col.db.sig = (2, 200)
    col.db.notes = [(1, 1, "彫刻\x1fちょうこく"), (3, 1, "語彙\x1fごい")]
    km.initialize()
    assert col.db.scan_calls == 1        # no full rescan
    assert col.db.fetch_ids == [3]       # field text only for the new note
    assert km.known_kanji_counts == Counter({"彫": 1, "刻": 1, "語": 1, "彙": 1})


def test_incremental_sync_recredits_edited_notes(fake_col):
    col = fake_col(notes=[(1, 1, "彫刻\x1fちょうこく")])
    km = KanjiManager(Config())
    km.initialize()

    col.mod = 2
    col.db.sig = (2, 200)
    col.db.notes = [(1, 2, "語彙\x1fごい")]  # same note, edited (mod bumped)
    km.initialize()
    assert col.db.fetch_ids == [1]  # old contribution replaced, not doubled
    assert km.known_kanji_counts == Counter({"語": 1, "彙": 1})


def test_incremental_sync_failure_falls_back_to_full_rescan(fake_col, monkeypatch):
    col = fake_col(notes=[(1, 1, "彫刻\x1fちょうこく")])
    km = KanjiManager(Config())
    km.initialize()

    col.mod = 2
    col.db.sig = (2, 200)
    col.db.notes = [(1, 1, "彫刻\x1fちょうこく"), (2, 1, "語\x1fご")]
    monkeypatch.setattr(km, "_sync_known_notes", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    km.initialize()
    assert col.db.scan_calls == 2  # fell back to the full rescan
    assert km.known_kanji_counts == Counter({"彫": 1, "刻": 1, "語": 1})


def test_last_scan_ms_set_on_rebuild_and_none_on_noop(fake_col):
    col = fake_col(notes=[(1, 1, "語\x1f")])
    km = KanjiManager(Config())
    km.initialize()
    assert isinstance(km.last_scan_ms, float)
    km.initialize()  # mod unchanged -> no-op
    assert km.last_scan_ms is None


def test_unknown_count_does_not_recheck_mod_once_initialized(fake_col):
    # Perf guard: get_unknown_kanji_count must not re-run the signature gate per
    # call — callers initialize once per batch.
    col = fake_col(notes=[(1, 1, "語\x1f")])
    km = KanjiManager(Config())
    km.initialize()
    first_calls = col.db.first_calls
    for _ in range(5):
        km.get_unknown_kanji_count("語彙")
    assert col.db.first_calls == first_calls


# --- singleton ------------------------------------------------------------------

def test_singleton_is_reused_and_resets_on_expression_field_change(fake_col):
    fake_col(notes=[(1, 1, "語\x1f")])
    km1 = get_kanji_manager(Config())
    km1.initialize()
    assert km1.initialized is True

    same = get_kanji_manager(Config())
    assert same is km1
    assert same.initialized is True  # same field -> cache kept

    changed = get_kanji_manager(Config(search_config=SearchConfig(expression_field="Word")))
    assert changed is km1
    assert changed.initialized is False  # field changed -> known set invalidated
    assert changed.known_kanji_counts == Counter()
    assert changed._note_kanji == {}     # snapshot invalidated with it
