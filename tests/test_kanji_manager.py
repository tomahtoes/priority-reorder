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
    def __init__(self, sig=(1, 100), rows=()):
        self.sig = sig          # returned by the signature query (db.first)
        self.rows = list(rows)  # flds strings returned by the scan (db.list)
        self.first_calls = 0
        self.list_calls = 0

    def first(self, sql):
        self.first_calls += 1
        return self.sig

    def list(self, sql):
        self.list_calls += 1
        return list(self.rows)


@pytest.fixture
def fake_col(monkeypatch):
    def build(mod=1, sig=(1, 100), rows=()):
        col = types.SimpleNamespace(mod=mod, models=_FakeModels(), db=_FakeDB(sig, rows))
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


# --- initialize() gating -------------------------------------------------------

def test_initialize_builds_known_counts_from_graduated_notes(fake_col):
    fake_col(rows=["彫刻\x1fちょうこく", "刻\x1fこく"])
    km = KanjiManager(Config())
    km.initialize()
    assert km.known_kanji_counts == Counter({"彫": 1, "刻": 2})


def test_initialize_skips_everything_when_mod_unchanged(fake_col):
    col = fake_col(rows=["語\x1f"])
    km = KanjiManager(Config())
    km.initialize()
    km.initialize()
    assert col.db.first_calls == 1  # signature queried once
    assert col.db.list_calls == 1   # scanned once


def test_initialize_rescans_only_when_known_signature_changes(fake_col):
    col = fake_col(rows=["語\x1f"])
    km = KanjiManager(Config())
    km.initialize()

    col.mod = 2  # collection changed, but the graduated set's signature did not
    km.initialize()
    assert col.db.first_calls == 2
    assert col.db.list_calls == 1  # no rescan

    col.mod = 3
    col.db.sig = (2, 200)  # the known set actually changed
    km.initialize()
    assert col.db.list_calls == 2  # rescanned


def test_unknown_count_does_not_recheck_mod_once_initialized(fake_col):
    # Perf guard: get_unknown_kanji_count must not re-run the signature gate per
    # call — callers initialize once per batch.
    col = fake_col(rows=["語\x1f"])
    km = KanjiManager(Config())
    km.initialize()
    first_calls = col.db.first_calls
    for _ in range(5):
        km.get_unknown_kanji_count("語彙")
    assert col.db.first_calls == first_calls


# --- singleton ------------------------------------------------------------------

def test_singleton_is_reused_and_resets_on_expression_field_change(fake_col):
    fake_col(rows=["語\x1f"])
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
