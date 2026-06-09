"""Unit tests for DataManager: search building, the custom-term post-filter fast
path, raw-count reporting, and the per-run caches.

A fake collection provides find_cards + the bulk-load SQL; rows are
(cid, nid, mid, flds) like the real `cards join notes` query returns.
"""

import types

import pytest

import data_manager as dmod
from config_manager import Config
from data_manager import DataManager


class _FakeModels:
    """mid=1 has Expression(0)/ExpressionReading(1)/FreqSort(2); mid=2 only Expression."""

    _MODELS = {
        1: {"id": 1, "name": "Full", "_fields": {"Expression": 0, "ExpressionReading": 1, "FreqSort": 2}},
        2: {"id": 2, "name": "Bare", "_fields": {"Expression": 0}},
    }

    def get(self, mid):
        return self._MODELS.get(mid)

    def field_map(self, model):
        return {name: (ord_, {"name": name}) for name, ord_ in model["_fields"].items()}


class _FakeDB:
    def __init__(self, rows_by_cid):
        self.rows_by_cid = rows_by_cid

    def all(self, sql):
        inside = sql[sql.rindex("(") + 1 : sql.rindex(")")]
        ids = [int(x) for x in inside.split(",") if x.strip()]
        return [self.rows_by_cid[cid] for cid in ids if cid in self.rows_by_cid]


class _FakeCol:
    def __init__(self, find_results, rows_by_cid):
        self.find_results = find_results  # final query string -> [cid]
        self.queries = []
        self.db = _FakeDB(rows_by_cid)
        self.models = _FakeModels()

    def find_cards(self, query):
        self.queries.append(query)
        return list(self.find_results.get(query, []))


def _row(cid, nid, expr="", reading="", freq="", mid=1):
    return (cid, nid, mid, "\x1f".join([expr, reading, freq]))


@pytest.fixture
def fake_col(monkeypatch):
    def build(find_results=None, rows=()):
        col = _FakeCol(find_results or {}, {r[0]: r for r in rows})
        monkeypatch.setattr(dmod.mw, "col", col, raising=False)
        return col

    return build


# --- query building (is:new scoping) -----------------------------------------

def test_or_query_is_wrapped_so_is_new_scopes_whole_query(fake_col):
    # Regression: Anki binds AND tighter than OR, so the unwrapped form
    # `deck:A or deck:B is:new` applied is:new to the last branch only.
    col = fake_col()
    DataManager(Config()).get_cards_from_search("deck:A or deck:B")
    assert col.queries == ["(deck:A or deck:B) is:new"]


def test_empty_query_searches_bare_is_new(fake_col):
    col = fake_col()
    DataManager(Config()).get_cards_from_search("")
    assert col.queries == ["is:new"]


def test_custom_term_fast_path_wraps_stripped_standard_part(fake_col):
    col = fake_col()
    DataManager(Config()).get_cards_from_search("deck:X f<100")
    assert col.queries == ["(deck:X) is:new"]


# --- search memoization -------------------------------------------------------

def test_search_results_are_memoized_per_query(fake_col):
    col = fake_col(
        find_results={"(deck:A) is:new": [1]},
        rows=[_row(1, 10, "語", "ご", "100")],
    )
    dm = DataManager(Config())
    r1 = dm.get_cards_from_search("deck:A")
    r2 = dm.get_cards_from_search("deck:A")
    assert col.queries == ["(deck:A) is:new"]  # find_cards hit exactly once
    assert [c.card_id for c in r1.cards] == [1] == [c.card_id for c in r2.cards]


# --- custom-term post-filtering ------------------------------------------------

def test_custom_freq_term_filters_and_reports_raw_count(fake_col):
    col = fake_col(
        find_results={"(deck:X) is:new": [1, 2]},
        rows=[_row(1, 10, "a", "r", "50"), _row(2, 20, "b", "r", "200")],
    )
    res = DataManager(Config()).get_cards_from_search("deck:X f<100")
    assert col.queries == ["(deck:X) is:new"]
    assert res.raw_count == 2  # standard-part matches before the custom filter
    assert [c.card_id for c in res.cards] == [1]


def test_negated_custom_term_inverts_the_filter(fake_col):
    fake_col(
        find_results={"(deck:X) is:new": [1, 2]},
        rows=[_row(1, 10, "a", "r", "50"), _row(2, 20, "b", "r", "200")],
    )
    res = DataManager(Config()).get_cards_from_search("deck:X -f<100")
    assert [c.card_id for c in res.cards] == [2]
    assert res.raw_count == 2


def test_plain_query_raw_count_equals_match_count(fake_col):
    fake_col(
        find_results={"(deck:A) is:new": [1]},
        rows=[_row(1, 10, "語", "ご", "100")],
    )
    res = DataManager(Config()).get_cards_from_search("deck:A")
    assert res.raw_count == len(res.cards) == 1


# --- bulk load field handling ---------------------------------------------------

def test_bulk_load_resolves_fields_and_sort_value(fake_col):
    fake_col(find_results={"is:new": [1]}, rows=[_row(1, 10, "彫刻", "ちょうこく", "123")])
    res = DataManager(Config()).get_cards_from_search("")
    data = res.cards[0].data
    assert data.expression == "彫刻"
    assert data.reading == "ちょうこく"
    assert data.sort_field_value == 123.0
    assert data.has_sort_value is True


def test_bulk_load_note_type_missing_fields_yields_no_sort_value(fake_col):
    # mid=2 lacks ExpressionReading/FreqSort entirely; missing fields resolve to ""
    fake_col(find_results={"is:new": [5]}, rows=[(5, 50, 2, "語")])
    res = DataManager(Config()).get_cards_from_search("")
    data = res.cards[0].data
    assert data.expression == "語"
    assert data.reading == ""
    assert data.has_sort_value is False


# --- per-run caches ---------------------------------------------------------------

def test_occ_count_cached_per_note_for_the_run(fake_col, monkeypatch):
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "語", "ご", "100")],
    )
    calls = {"n": 0}

    def fake_occurrence_count(dict_names, expression, reading, **kwargs):
        calls["n"] += 1
        return 7

    monkeypatch.setattr(dmod, "occurrence_count", fake_occurrence_count)

    dm = DataManager(Config())
    r1 = dm.get_cards_from_search("deck:X occurrences:D>5")
    r2 = dm.get_cards_from_search("deck:X occurrences:D>5")
    assert [c.card_id for c in r1.cards] == [1] == [c.card_id for c in r2.cards]
    assert calls["n"] == 1  # memoized by (dicts, note id) across the whole run


def test_occ_predicate_never_matches_without_expression_or_reading(fake_col, monkeypatch):
    fake_col(
        find_results={"(deck:X) is:new": [1, 2]},
        rows=[_row(1, 10, "語", "", "100"), _row(2, 20, "語", "ご", "100")],
    )
    monkeypatch.setattr(dmod, "occurrence_count", lambda *a, **k: 99)
    res = DataManager(Config()).get_cards_from_search("deck:X occurrences:D>5")
    assert [c.card_id for c in res.cards] == [2]  # card 1 has no reading
