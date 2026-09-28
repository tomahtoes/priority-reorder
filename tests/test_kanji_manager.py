"""Unit tests for KanjiManager: kanji extraction/counting, the signature-gated
known-kanji scan, and the singleton's reset-on-field-change behavior."""

import types
from collections import Counter

import pytest

import kanji_manager as kmod
import kanji_readings as kr
from config_manager import Config, SearchConfig
from kanji_manager import KanjiManager, get_kanji_manager
from utils import is_kanji


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



def test_get_kanji_count_counts_cjk_chars_only():
    km = KanjiManager(Config())
    assert km.get_kanji_count("彫刻abcの12") == 2
    assert km.get_kanji_count("ひらがなカナ") == 0


def test_get_kanji_count_uses_the_shared_kanji_class():
    # This used to be a local `[一-龯]`, narrower than utils.is_kanji, so kanji:num and
    # kanji:new silently ignored characters variant matching counted.
    km = KanjiManager(Config())
    assert km.get_kanji_count("𠮟る") == 1   # U+20B9F, Ext B
    assert km.get_kanji_count("﨑") == 1      # U+FA11, compatibility ideograph
    assert km._extract_kanji("彫刻𠮟﨑の") == [c for c in "彫刻𠮟﨑の" if is_kanji(c)]


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


# initialize() gating

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


# generation / last_delta: what data_manager's cross-run kanji memos key on

def test_rebuild_bumps_generation_without_a_delta(fake_col):
    fake_col(notes=[(1, 1, "語\x1f")])
    km = KanjiManager(Config())
    assert km.generation == 0
    km.initialize()
    assert km.generation == 1
    assert km.last_delta is None  # a rebuild invalidates everything


def test_sync_delta_names_the_kanji_of_graduated_and_departed_notes(fake_col):
    col = fake_col(notes=[(1, 1, "彫刻\x1fちょうこく"), (2, 1, "刻\x1fこく")])
    km = KanjiManager(Config())
    km.initialize()

    col.mod = 2
    col.db.sig = (2, 200)
    col.db.notes = [(1, 1, "彫刻\x1fちょうこく"), (3, 1, "語彙\x1fごい")]
    km.initialize()
    assert km.generation == 2
    # Note 2 left (刻), note 3 graduated (語彙). Note 1 is untouched, so 彫 is not named.
    assert km.last_delta == (1, frozenset("刻語彙"), frozenset())


def test_sync_delta_covers_an_edited_notes_old_and_new_kanji(fake_col):
    col = fake_col(notes=[(1, 1, "彫刻\x1fちょうこく")])
    km = KanjiManager(Config())
    km.initialize()

    col.mod = 2
    col.db.sig = (2, 200)
    col.db.notes = [(1, 2, "語彙\x1fごい")]
    km.initialize()
    assert km.last_delta == (1, frozenset("彫刻語彙"), frozenset())


def test_review_only_signature_change_keeps_the_generation(fake_col):
    # Reviews move card mtimes (the signature) without re-crediting any note, so every
    # memoized count downstream is still right.
    col = fake_col(notes=[(1, 1, "語\x1f")])
    km = KanjiManager(Config())
    km.initialize()

    col.mod = 2
    col.db.sig = (1, 150)
    km.initialize()
    assert col.db.member_calls == 1
    assert km.generation == 1


def test_failed_sync_falls_back_to_a_rebuild_generation(fake_col, monkeypatch):
    col = fake_col(notes=[(1, 1, "彫刻\x1fちょうこく")])
    km = KanjiManager(Config())
    km.initialize()

    col.mod = 2
    col.db.sig = (2, 200)
    monkeypatch.setattr(km, "_sync_known_notes", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    km.initialize()
    assert km.generation == 2
    assert km.last_delta is None


def test_last_scan_ms_set_on_rebuild_and_none_on_noop(fake_col):
    col = fake_col(notes=[(1, 1, "語\x1f")])
    km = KanjiManager(Config())
    km.initialize()
    assert isinstance(km.last_scan_ms, float)
    km.initialize()  # mod unchanged -> no-op
    assert km.last_scan_ms is None


def test_unknown_count_does_not_recheck_mod_once_initialized(fake_col):
    # Perf guard: get_unknown_kanji_count must not re-run the signature gate per
    # call, since callers initialize once per batch.
    col = fake_col(notes=[(1, 1, "語\x1f")])
    km = KanjiManager(Config())
    km.initialize()
    first_calls = col.db.first_calls
    for _ in range(5):
        km.get_unknown_kanji_count("語彙")
    assert col.db.first_calls == first_calls


# singleton

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


# reading slots (kanji:new_reading)

def _reading_config():
    return Config(search_config=SearchConfig(
        expression_field="Expression", expression_reading_field="Reading"))


def _reading_col(monkeypatch, notes, mod=1, sig=(1, 100)):
    col = types.SimpleNamespace(
        mod=mod,
        models=_FakeModels(fields=("Expression", "Reading")),
        db=_FakeDB(sig, notes),
    )
    monkeypatch.setattr(kmod.mw, "col", col, raising=False)
    return col


def _note(nid, expression, reading, nmod=1):
    return (nid, nmod, expression + "\x1f" + reading)


def test_reading_counts_are_not_built_until_asked(monkeypatch):
    """kanji:new/kanji:num must not pay for the reading index."""
    _reading_col(monkeypatch, [_note(1, "食事", "しょくじ")])
    km = KanjiManager(_reading_config())
    km.initialize()
    assert km.known_kanji_counts == Counter({"食": 1, "事": 1})
    assert km.known_reading_counts == Counter()
    assert km.unresolved_reading_rate() is None


def test_enable_readings_rebuilds_and_credits_slots(monkeypatch):
    _reading_col(monkeypatch, [_note(1, "食事", "しょくじ")])
    km = KanjiManager(_reading_config())
    km.initialize()
    km.enable_readings()
    km.initialize()
    assert km.known_reading_counts == Counter(kr.reading_slots("食事", "しょくじ"))


def test_a_learned_reading_silences_only_that_reading(monkeypatch):
    """The whole point of the term: 食事 teaches 食=しょく, which must not make
    食べる (食=た) look known."""
    _reading_col(monkeypatch, [_note(1, "食事", "しょくじ")])
    km = KanjiManager(_reading_config())
    km.enable_readings()
    km.initialize()
    assert km.get_new_reading_count("食事", "しょくじ") == 0
    assert km.get_new_reading_count("食べる", "たべる") == 1


def test_rendaku_does_not_count_as_a_new_reading(monkeypatch):
    _reading_col(monkeypatch, [_note(1, "血", "ち")])
    km = KanjiManager(_reading_config())
    km.enable_readings()
    km.initialize()
    # 鼻 is genuinely new; 血 surfaces as ぢ but is the same reading.
    assert km.get_new_reading_count("鼻血", "はなぢ") == 1


def test_target_counts_words_per_reading_not_per_kanji(monkeypatch):
    """kanji:new_reading[N] holds a reading "new" until N learned words use it,
    exactly as kanji:new[N] does for the kanji itself."""
    _reading_col(monkeypatch, [
        _note(1, "可愛い", "かわいい"),
        _note(2, "可愛らしい", "かわいらしい"),
    ])
    km = KanjiManager(_reading_config())
    km.enable_readings()
    km.initialize()
    # Both words credit the same slot for 愛, so they accumulate toward one bar.
    slot = kr.reading_slots("可愛い", "かわいい")[1]
    assert km.known_reading_counts[slot] == 2
    # At [2] the bar is met, so nothing in 可愛がる is new. At [3] neither 可 nor
    # 愛 has reached it yet, so both count. The term is per kanji, not per word.
    assert km.get_new_reading_count("可愛がる", "かわいがる", 2) == 0
    assert km.get_new_reading_count("可愛がる", "かわいがる", 3) == 2
    # 愛=あい is a different reading and is untouched by any of them.
    assert km.get_new_reading_count("愛情", "あいじょう", 1) == 2


def test_unresolved_rate_tracks_the_known_set(monkeypatch):
    _reading_col(monkeypatch, [
        _note(1, "食事", "しょくじ"),   # both resolved
        _note(2, "火傷", "やけど"),     # both unresolved
    ])
    km = KanjiManager(_reading_config())
    km.enable_readings()
    km.initialize()
    assert km.unresolved_reading_rate() == pytest.approx(0.5)


def test_unresolved_rate_is_total_when_the_reading_field_is_wrong(monkeypatch):
    """A reading field holding something that is not the reading leaves
    everything unresolved, the signal the diagnostic exists to surface."""
    _reading_col(monkeypatch, [
        _note(1, "食事", "meal"), _note(2, "勉強", "study"),
    ])
    km = KanjiManager(_reading_config())
    km.enable_readings()
    km.initialize()
    assert km.unresolved_reading_rate() == 1.0


def test_notes_without_the_reading_field_contribute_no_slots(monkeypatch):
    col = types.SimpleNamespace(
        mod=1, models=_FakeModels(fields=("Expression",)),
        db=_FakeDB((1, 100), [(1, 1, "食事")]))
    monkeypatch.setattr(kmod.mw, "col", col, raising=False)
    km = KanjiManager(_reading_config())
    km.enable_readings()
    km.initialize()
    # Still a known kanji. Membership keys off the expression field alone.
    assert km.known_kanji_counts == Counter({"食": 1, "事": 1})
    assert km.known_reading_counts == Counter()


def test_incremental_sync_keeps_reading_counts_correct(monkeypatch):
    col = _reading_col(monkeypatch, [_note(1, "食事", "しょくじ")])
    km = KanjiManager(_reading_config())
    km.enable_readings()
    km.initialize()
    # A note leaves the known set; its slots must be subtracted, not stranded.
    col.db.notes = []
    col.db.sig = (0, 0)
    col.mod = 2
    km.initialize()
    assert km.known_reading_counts == Counter()
    assert km.unresolved_reading_rate() is None
    assert km.last_delta == (km.generation - 1, frozenset("食事"),
                             frozenset(kr.reading_slots("食事", "しょくじ")))


def test_singleton_resets_when_the_reading_field_is_renamed(monkeypatch):
    _reading_col(monkeypatch, [_note(1, "食事", "しょくじ")])
    km = get_kanji_manager(_reading_config())
    km.enable_readings()
    km.initialize()
    assert km.known_reading_counts
    renamed = get_kanji_manager(Config(search_config=SearchConfig(
        expression_field="Expression", expression_reading_field="Kana")))
    assert renamed is km
    assert renamed.known_reading_counts == Counter()
    assert renamed._note_kanji == {}
