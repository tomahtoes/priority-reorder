"""Unit tests for DataManager: search building, the custom-term post-filter fast
path, raw-count reporting, and the per-run + cross-run caches.

A fake collection provides find_cards + the two bulk-load SQL shapes (the
cards->notes linkage pass and the notes field fetch); rows are
(cid, nid, mid, flds) and note mods default to 1 unless a test bumps them.
"""

import math
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
    """Serves the two bulk-load query shapes: phase 1 (cards join notes ->
    (cid, nid, mid, n.mod), no field text) and phase 2 (notes by id ->
    (nid, mid, mod, flds))."""

    def __init__(self, rows_by_cid):
        self.rows_by_cid = rows_by_cid
        self.note_mods = {}   # nid -> mod, default 1 (bump to simulate a note edit)
        self.all_calls = 0
        self.link_calls = 0   # phase-1 linkage queries
        self.flds_calls = 0   # phase-2 field-text queries
        self.flds_ids = []    # nids requested by phase-2 queries
        self.link_sql = []    # verbatim phase-1 SQL, for the join-order guard

    @staticmethod
    def _ids(sql):
        inside = sql[sql.rindex("(") + 1 : sql.rindex(")")]
        return [int(x) for x in inside.split(",") if x.strip()]

    def all(self, sql):
        self.all_calls += 1
        ids = self._ids(sql)
        if "from cards c" in sql:
            self.link_calls += 1
            self.link_sql.append(sql)
            return [
                (cid, r[1], r[2], self.note_mods.get(r[1], 1))
                for cid in ids
                if (r := self.rows_by_cid.get(cid)) is not None
            ]
        self.flds_calls += 1
        self.flds_ids.extend(ids)
        notes = {
            nid: (nid, mid, self.note_mods.get(nid, 1), flds)
            for _cid, nid, mid, flds in self.rows_by_cid.values()
        }
        return [notes[nid] for nid in ids if nid in notes]


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


@pytest.fixture(autouse=True)
def _clear_note_cache():
    # The cross-run note cache is module state; isolate every test.
    dmod.clear_note_cache()
    yield
    dmod.clear_note_cache()


@pytest.fixture
def fake_col(monkeypatch):
    def build(find_results=None, rows=()):
        col = _FakeCol(find_results or {}, {r[0]: r for r in rows})
        monkeypatch.setattr(dmod.mw, "col", col, raising=False)
        return col

    return build


# query building (is:new scoping)

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


# search memoization

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


# custom-term post-filtering

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


def test_custom_length_term_fast_path_filters_by_expression_length(fake_col):
    fake_col(
        find_results={"(deck:X) is:new": [1, 2, 3, 4]},
        rows=[_row(1, 10, "", "", "1"), _row(2, 20, "手", "て", "1"),
              _row(3, 30, "茶の間", "ちゃのま", "1"), _row(4, 40, "下駄箱", "げたばこ", "1")],
    )
    dm = DataManager(Config())
    assert [c.card_id for c in dm.get_cards_from_search("deck:X length>=3").cards] == [3, 4]
    assert [c.card_id for c in dm.get_cards_from_search("deck:X length=1").cards] == [2]
    # No empty-expression skip: the empty field counts as length 0.
    assert [c.card_id for c in dm.get_cards_from_search("deck:X length=0").cards] == [1]
    assert [c.card_id for c in dm.get_cards_from_search("deck:X -length>=3").cards] == [1, 2]


def test_first_letter_aliases_ride_the_same_fast_path(fake_col):
    # has_custom_term, _strip_custom_terms and parse_custom_terms each match the aliases
    # separately. If any one of them missed, the query would either skip the fast path or
    # strip to a base the others do not agree on.
    fake_col(
        find_results={"(deck:X) is:new": [1, 2, 3, 4]},
        rows=[_row(1, 10, "", "", "1"), _row(2, 20, "手", "て", "1"),
              _row(3, 30, "茶の間", "ちゃのま", "1"), _row(4, 40, "下駄箱", "げたばこ", "1")],
    )
    dm = DataManager(Config())
    assert [c.card_id for c in dm.get_cards_from_search("deck:X l>=3").cards] == [3, 4]
    assert [c.card_id for c in dm.get_cards_from_search("deck:X -l>=3").cards] == [1, 2]


def test_filter_order_does_not_change_the_result(fake_col, monkeypatch):
    # The post-filter reorders terms cheapest-first, which is only sound because they form
    # a pure conjunction of independent predicates. Whatever order the query writes them
    # in, the surviving cards and their order must be identical.
    counts = {10: 9, 20: 1, 30: 9, 40: 1}

    def build():
        fake_col(
            find_results={"(deck:X) is:new": [1, 2, 3, 4]},
            rows=[_row(1, 10, "茶の間", "ちゃのま", "50"), _row(2, 20, "茶の間", "ちゃのま", "50"),
                  _row(3, 30, "手", "て", "500"), _row(4, 40, "手", "て", "500")],
        )
        monkeypatch.setattr(
            dmod, "occurrence_counter",
            lambda *a, **k: (lambda expression, reading, card_kanji=None: 0),
            raising=False,
        )
        return DataManager(Config())

    a = build().get_cards_from_search("deck:X f<100 length>=3")
    b = build().get_cards_from_search("deck:X length>=3 f<100")
    assert [c.card_id for c in a.cards] == [c.card_id for c in b.cards] == [1, 2]
    assert a.raw_count == b.raw_count == 4


def test_expensive_predicate_is_never_built_once_nothing_is_left(fake_col, monkeypatch):
    # Building the kanji predicate initializes the known-kanji set (a collection scan), and
    # the seen predicate resolves and parses the daily dicts. A cheap term that already
    # emptied the candidate list must not pay for either.
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "語", "ご", "5000")],
    )

    class _FakeKM:
        def __init__(self):
            self.inits = 0

        def initialize(self):
            self.inits += 1

        def get_unknown_kanji_count(self, text, target=1):
            return 1

    km = _FakeKM()
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: km)

    res = DataManager(Config()).get_cards_from_search("deck:X kanji:new>=1 f<100")
    assert [c.card_id for c in res.cards] == []  # f<100 eliminates the only card
    assert km.inits == 0, "kanji predicate must not be built for an empty candidate list"


def test_plain_query_raw_count_equals_match_count(fake_col):
    fake_col(
        find_results={"(deck:A) is:new": [1]},
        rows=[_row(1, 10, "語", "ご", "100")],
    )
    res = DataManager(Config()).get_cards_from_search("deck:A")
    assert res.raw_count == len(res.cards) == 1


# non-conjunctive custom-term queries (candidate-restricted resolution)

def _nid_sets(query):
    """The nid clauses in a rewritten query, as sets, since the resolvers return sets."""
    import re as _re
    return [{int(x) for x in m.group(1).split(",")}
            for m in _re.finditer(r"nid:([0-9,]+)", query)]


def test_grouped_custom_term_resolves_over_the_candidate_set(fake_col):
    """A query the conjunctive post-filter cannot take still resolves over its own candidates.

    `length>=2` sits inside an OR group, so the Python post-filter path is unavailable, but
    deck:A still bounds the answer. Before this path such queries went to the patched
    find_cards, which resolved every term against the whole collection.
    """
    col = fake_col(
        find_results={"(deck:A) is:new": [1, 2, 3]},
        rows=[
            _row(1, 10, "語", "ご", "500"),      # 1 char, freq 500
            _row(2, 20, "学校", "がっこう", "50"),   # 2 chars, freq 50
            _row(3, 30, "日本語", "にほんご", "900"),  # 3 chars, freq 900
        ],
    )
    DataManager(Config()).get_cards_from_search("deck:A (length>=2 OR added:3) f>=100")

    assert col.queries[0] == "(deck:A) is:new"     # candidates from the standard conjuncts
    final = col.queries[-1]
    assert "length" not in final and "f>=" not in final   # every custom token resolved away
    assert _nid_sets(final) == [{20, 30}, {10, 30}]       # length>=2, then f>=100


def test_candidate_query_is_shared_with_the_conjunctive_path(fake_col):
    # Both paths ask for "(deck:A) is:new", so _search_cache serves the second one for free.
    col = fake_col(
        find_results={"(deck:A) is:new": [1]},
        rows=[_row(1, 10, "語", "ご", "500")],
    )
    dm = DataManager(Config())
    dm.get_cards_from_search("deck:A f>=100")
    dm.get_cards_from_search("deck:A (length>=2 OR added:3) f>=100")
    assert col.queries.count("(deck:A) is:new") == 1


def test_raw_count_reports_the_candidate_total(fake_col):
    # Mirrors _get_cards_filtered: raw_count is the standard part's match count, before any
    # custom term narrowed it, which is what the summary window reports.
    col = fake_col(
        find_results={"(deck:A) is:new": [1, 2]},
        rows=[_row(1, 10, "語", "ご", "500"), _row(2, 20, "学校", "が", "50")],
    )
    result = DataManager(Config()).get_cards_from_search("deck:A (length>=2 OR added:3) f>=100")
    assert result.raw_count == 2


def test_top_level_or_still_falls_back_to_find_cards(fake_col):
    # No standard conjunct bounds a top-level disjunction, so the whole raw query goes to the
    # patched find_cards exactly as before.
    col = fake_col()
    DataManager(Config()).get_cards_from_search("deck:A or f>=100")
    assert col.queries == ["(deck:A or f>=100) is:new"]


def test_bare_custom_term_still_falls_back_to_find_cards(fake_col):
    col = fake_col()
    DataManager(Config()).get_cards_from_search("f>=100")
    assert col.queries == ["(f>=100) is:new"]


def test_failed_rewrite_falls_back_to_find_cards(fake_col, monkeypatch):
    # Bailing costs speed, never correctness: a broken rewrite must not lose the search.
    col = fake_col(find_results={"(deck:A) is:new": []})

    def boom(*a, **k):
        raise RuntimeError("rewrite exploded")

    monkeypatch.setattr(dmod, "rewrite_query", boom)
    DataManager(Config()).get_cards_from_search("deck:A (length>=2 OR added:3) f>=100")
    assert col.queries[-1] == "(deck:A (length>=2 OR added:3) f>=100) is:new"


# kanji reading mode (whole-config scan)

class _KMStub:
    """Counts known-set scans, and mirrors the real manager's rule that turning reading mode
    on discards whatever the previous scan built."""

    def __init__(self):
        self.reading_mode = False
        self.initialized = False
        self.scans = 0

    def enable_readings(self):
        if self.reading_mode:
            return
        self.reading_mode = True
        self.initialized = False

    def initialize(self):
        if self.initialized:
            return
        self.scans += 1
        self.initialized = True


@pytest.fixture
def km_stub(monkeypatch):
    stub = _KMStub()
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: stub)
    return stub


def test_reading_mode_comes_from_the_config_so_the_known_set_is_scanned_once(fake_col, km_stub):
    """A config mixing kanji:new with kanji:new_reading must scan the learned collection once.

    Reading slots are collected during the scan, so enabling them afterwards throws that scan
    away. Built in search order, kanji:new scanned without slots and the later
    kanji:new_reading rebuilt from scratch (measured 505 ms discarded, then 762 ms kept). The
    mode is therefore decided from the whole config before any predicate triggers a scan.
    """
    fake_col()
    cfg = Config(priority_search=["deck:X kanji:new>=1", "deck:X kanji:new_reading>=1"])
    dm = DataManager(cfg)
    dm._term_predicate("kanji", ("new", 1, ">=", 1))
    dm._term_predicate("kanji", ("new_reading", 1, ">=", 1))
    assert km_stub.reading_mode
    assert km_stub.scans == 1


def test_reading_mode_stays_off_when_no_configured_search_asks(fake_col, km_stub):
    # Users of kanji:new / kanji:num must keep paying nothing for the reading index.
    fake_col()
    dm = DataManager(Config(priority_search=["deck:X kanji:new>=1"]))
    dm._term_predicate("kanji", ("new", 1, ">=", 1))
    assert not km_stub.reading_mode


def test_normal_search_also_decides_reading_mode(fake_col, km_stub):
    # _configured_queries covers the normal search, not just the priority ones.
    fake_col()
    dm = DataManager(Config(normal_search="deck:X kanji:new_reading>=1"))
    assert dm._reading_mode_configured()


def test_malformed_search_does_not_break_the_reading_mode_scan(fake_col, km_stub):
    fake_col()
    dm = DataManager(Config(priority_search=["deck:X ((((", "deck:Y kanji:new_reading>=1"]))
    assert dm._reading_mode_configured()


# get_cards / bulk load batching

def test_get_cards_bulk_loads_in_one_query(fake_col):
    # The reorderer hands every candidate id to get_cards in one call; the load
    # must be bulk SQL passes (one linkage + one field fetch on a cold cache),
    # not one query per id (the get_card miss path).
    col = fake_col(rows=[_row(i, i * 10, "語", "ご", "1") for i in range(1, 6)])
    out = DataManager(Config()).get_cards([1, 2, 3, 4, 5])
    assert sorted(out) == [1, 2, 3, 4, 5]
    assert col.db.link_calls == 1
    assert col.db.flds_calls == 1


def test_get_cards_drops_vanished_ids(fake_col):
    col = fake_col(rows=[_row(1, 10, "語", "ご", "1")])
    out = DataManager(Config()).get_cards([1, 999])
    assert list(out) == [1]


def test_bulk_load_forces_the_join_order(fake_col):
    """The linkage query must pin cards as the outer table.

    Left to choose, SQLite abandons the primary-key lookup once the inlined id list passes
    roughly 2500 and scans notes instead, which costs it every row's inline field text to
    return two integers (measured 22 ms against 7.2 s on a 22k-note collection). `cross join`
    is an ordinary inner join that also forbids the reorder, so this is a correctness-neutral
    way to keep the plan. Reverting it to a plain `join` reintroduces a multi-second stall.
    """
    col = fake_col(rows=[_row(i, i * 10, "語", "ご", "1") for i in range(1, 4)])
    DataManager(Config()).get_cards([1, 2, 3])
    assert col.db.link_sql
    for sql in col.db.link_sql:
        assert "cross join notes" in sql


def test_bulk_chunk_size_stays_on_the_primary_key_plan(fake_col):
    # Above ~2500 inlined ids SQLite switches `notes.id in (...)` from the primary key to a
    # full table scan, so the chunk size has a ceiling as well as the statement-length one.
    assert dmod._BULK_CHUNK_SIZE <= 2500


def test_bulk_load_chunks_large_id_lists(fake_col):
    n = 2 * dmod._BULK_CHUNK_SIZE + 200  # forces 3 chunks per phase
    ids = list(range(1, n + 1))
    col = fake_col(rows=[_row(i, i, "語", "ご", "1") for i in ids])
    out = DataManager(Config()).get_cards(ids)
    assert len(out) == n
    expected = math.ceil(n / dmod._BULK_CHUNK_SIZE)
    assert col.db.link_calls == expected
    assert col.db.flds_calls == expected  # cold cache: every note fetched


# bulk load field handling

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


# per-run caches

def test_occ_count_cached_per_note_for_the_run(fake_col, monkeypatch):
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "語", "ご", "100")],
    )
    calls = {"n": 0}

    def fake_occurrence_counter(dict_names, **kwargs):
        def count(expression, reading, card_kanji=None):
            calls["n"] += 1
            return 7
        return count

    monkeypatch.setattr(dmod, "occurrence_counter", fake_occurrence_counter)

    dm = DataManager(Config())
    r1 = dm.get_cards_from_search("deck:X occurrences:D>5")
    r2 = dm.get_cards_from_search("deck:X occurrences:D>5")
    assert [c.card_id for c in r1.cards] == [1] == [c.card_id for c in r2.cards]
    assert calls["n"] == 1  # memoized by (dicts, note id) across the whole run


def test_occ_predicate_forwards_all_matching_flags(fake_col, monkeypatch):
    # Every config matching flag must reach occurrence_counter. A flag that stops being
    # forwarded silently degrades to the exact-match behavior. The flags are bound when the
    # counter is BUILT (once per predicate), not per card.
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "煌めく", "きらめく", "100")],
    )
    built = {}
    calls = []

    def fake_occurrence_counter(dict_names, **kwargs):
        built.update(kwargs)

        def count(expression, reading, card_kanji=None):
            calls.append((expression, reading, card_kanji))
            return 7

        return count

    monkeypatch.setattr(dmod, "occurrence_counter", fake_occurrence_counter)
    cfg = Config(kana_normalization=True, combine_word_forms=True, prefix_matching=True,
                 suffix_matching=True, variant_matching=True, stem_matching=True,
                 compound_matching=True, honorific_folding=True)
    DataManager(cfg).get_cards_from_search("deck:X occurrences:D>5")
    matching_flags = {k: v for k, v in built.items() if k != "prefolded"}
    assert matching_flags == dict(normalize_kana=True, combine_word_forms=True,
                                  prefix_matching=True, suffix_matching=True,
                                  variant_matching=True, stem_matching=True,
                                  compound_matching=True, honorific_folding=True)
    # The note is folded and skeletonized once per run and handed down, so the counter
    # must be told not to redo either (see DataManager._note_derived).
    assert built["prefolded"] is True
    assert calls == [("煌めく", "きらめく", "煌")]


def test_kanji_count_cache_is_keyed_by_target(fake_col, monkeypatch):
    # Regression guard: two kanji:new searches with different [T] targets in the
    # same run must not share cached counts, so the cache key includes the target.
    fake_col(
        find_results={"(deck:X) is:new": [1, 2]},
        rows=[_row(1, 10, "語", "ご", "100"), _row(2, 20, "彙", "い", "100")],
    )

    class _FakeKM:
        def __init__(self):
            self.calls = []

        def initialize(self):
            pass

        def get_unknown_kanji_count(self, text, target=1):
            self.calls.append((text, target))
            return 0 if target == 1 else 1

    km = _FakeKM()
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: km)

    dm = DataManager(Config())
    r1 = dm.get_cards_from_search("deck:X kanji:new[1]>=1")
    r2 = dm.get_cards_from_search("deck:X kanji:new[2]>=1")
    assert [c.card_id for c in r1.cards] == []       # target 1 -> count 0 everywhere
    assert [c.card_id for c in r2.cards] == [1, 2]   # target 2 -> count 1, not the cached 0
    assert set(km.calls) == {("語", 1), ("彙", 1), ("語", 2), ("彙", 2)}


def test_occ_predicate_never_matches_without_expression_or_reading(fake_col, monkeypatch):
    fake_col(
        find_results={"(deck:X) is:new": [1, 2]},
        rows=[_row(1, 10, "語", "", "100"), _row(2, 20, "語", "ご", "100")],
    )
    monkeypatch.setattr(dmod, "occurrence_counter", lambda *a, **k: lambda *args: 99)
    res = DataManager(Config()).get_cards_from_search("deck:X occurrences:D>5")
    assert [c.card_id for c in res.cards] == [2]  # card 1 has no reading


# seen: on the fast path

class _FakeWindow:
    def __init__(self, present):
        self.present = present
        self.calls = []

    def contains(self, expression, reading, **flags):
        self.calls.append((expression, reading, flags))
        return expression in self.present


def test_custom_seen_term_fast_path_filters_via_window(fake_col, monkeypatch):
    # `deck:X seen:3` must ride the conjunctive fast path (no paren-wrapped
    # find_cards fallback): window resolved once, per-card membership in Python,
    # empty-expression cards never match, mirroring resolve_seen.
    fake_col(
        find_results={"(deck:X) is:new": [1, 2, 3]},
        rows=[_row(1, 10, "下駄", "げた", "50"), _row(2, 20, "茶", "ちゃ", "60"),
              _row(3, 30, "", "", "70")],
    )
    window = _FakeWindow(present={"下駄"})
    built = []
    monkeypatch.setattr(
        dmod.seen_manager, "get_seen_window",
        lambda n, kana, honorific, variant, stem, compound, today=None:
            built.append((n, kana, honorific, variant, stem, compound)) or window,
    )

    cfg = Config(prefix_matching=True)
    res = DataManager(cfg).get_cards_from_search("deck:X seen:3")

    assert [c.card_id for c in res.cards] == [1]
    assert built == [(3, False, False, False, False, False)]  # window resolved once
    assert [c[:2] for c in window.calls] == [("下駄", "げた"), ("茶", "ちゃ")]  # empty expr skipped
    assert all(f["prefix_matching"] is True for _, _, f in window.calls)  # config flags forwarded


def test_custom_seen_zero_fast_path_matches_nothing(fake_col, monkeypatch):
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "語", "ご", "1")],
    )

    def boom(*a, **k):
        raise AssertionError("seen:0 must not build a window")

    monkeypatch.setattr(dmod.seen_manager, "get_seen_window", boom)
    res = DataManager(Config()).get_cards_from_search("deck:X seen:0")
    assert res.cards == []
    assert res.raw_count == 1


def test_seen_contains_memoized_per_note(fake_col, monkeypatch):
    # Cards 1 and 2 share note 10, and the same seen:3 term runs in two
    # searches, so contains must be called exactly once per distinct note.
    fake_col(
        find_results={"(deck:X) is:new": [1, 2]},
        rows=[_row(1, 10, "下駄", "げた", "50"), _row(2, 10, "下駄", "げた", "50")],
    )
    window = _FakeWindow(present={"下駄"})
    monkeypatch.setattr(dmod.seen_manager, "get_seen_window",
                        lambda n, k, h, v, s, c, today=None: window)

    dm = DataManager(Config())
    r1 = dm.get_cards_from_search("deck:X seen:3")
    r2 = dm.get_cards_from_search("deck:X seen:3")
    assert [c.card_id for c in r1.cards] == [1, 2] == [c.card_id for c in r2.cards]
    assert len(window.calls) == 1


def test_seen_memo_keyed_by_n(fake_col, monkeypatch):
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "下駄", "げた", "50")],
    )
    window = _FakeWindow(present={"下駄"})
    monkeypatch.setattr(dmod.seen_manager, "get_seen_window",
                        lambda n, k, h, v, s, c, today=None: window)

    dm = DataManager(Config())
    dm.get_cards_from_search("deck:X seen:2")
    dm.get_cards_from_search("deck:X seen:3")
    assert len(window.calls) == 2  # different n -> no cross-n bleed


# cross-run note cache

def test_warm_run_skips_field_fetch_for_unchanged_notes(fake_col):
    rows = [_row(1, 10, "語", "ご", "100"), _row(2, 20, "彙", "い", "200")]
    col1 = fake_col(rows=rows)
    DataManager(Config()).get_cards([1, 2])
    assert col1.db.flds_calls == 1

    col2 = fake_col(rows=rows)  # fresh run over an unchanged collection
    out = DataManager(Config()).get_cards([1, 2])
    assert col2.db.link_calls == 1
    assert col2.db.flds_calls == 0  # every note served from the cross-run cache
    assert out[1].data.expression == "語"
    assert out[2].data.sort_field_value == 200.0


def test_note_edit_refetches_only_that_note(fake_col):
    rows = [_row(1, 10, "語", "ご", "100"), _row(2, 20, "彙", "い", "200")]
    fake_col(rows=rows)
    DataManager(Config()).get_cards([1, 2])

    rows2 = [_row(1, 10, "新", "しん", "100"), _row(2, 20, "彙", "い", "200")]
    col2 = fake_col(rows=rows2)
    col2.db.note_mods[10] = 2  # note 10 edited since the previous run
    out = DataManager(Config()).get_cards([1, 2])
    assert col2.db.flds_ids == [10]  # only the edited note's text is fetched
    assert out[1].data.expression == "新"
    assert out[2].data.expression == "彙"


def test_config_field_change_invalidates_note_cache(fake_col):
    rows = [_row(1, 10, "語", "ご", "100")]
    fake_col(rows=rows)
    DataManager(Config()).get_cards([1])

    col2 = fake_col(rows=rows)
    DataManager(Config(sort_field="Other")).get_cards([1])
    assert col2.db.flds_calls == 1  # fingerprint changed -> cache dropped


def test_clear_note_cache_empties_the_cache(fake_col):
    fake_col(rows=[_row(1, 10, "語", "ご", "100")])
    DataManager(Config()).get_cards([1])
    assert dmod._note_data_cache
    dmod.clear_note_cache()
    assert not dmod._note_data_cache


# sub-stage timing accumulators

def test_stage_ms_accumulates_substage_keys(fake_col):
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "語", "ご", "50")],
    )
    dm = DataManager(Config())
    dm.get_cards_from_search("deck:X f<100")
    for key in ("fc", "load", "filter_freq"):
        assert key in dm.stage_ms, key
        assert dm.stage_ms[key] >= 0.0


# nested seen windows
#
# seen:1 ⊆ seen:7 ⊆ seen:30, and SeenWindow.contains is monotone in the underlying union
# sets, so the largest window decides every miss in one probe. These pin both halves: that
# the short-circuit actually fires, and that it never changes an answer.

class _NestedWindows:
    """Stands in for seen_manager, serving genuinely nested windows and counting probes."""

    def __init__(self, by_level):
        self.windows = {n: _FakeWindow(present=p) for n, p in by_level.items()}
        self.todays = []

    def get_seen_window(self, n, kana, honorific, variant, stem, compound, today=None):
        self.todays.append(today)
        return self.windows[n]

    def probes(self, n):
        return len(self.windows[n].calls)


def _nested(monkeypatch, by_level):
    fake = _NestedWindows(by_level)
    monkeypatch.setattr(dmod.seen_manager, "get_seen_window", fake.get_seen_window)
    return fake


def test_seen_miss_at_largest_window_settles_every_smaller_level(fake_col, monkeypatch):
    # 茶 is in no window. The seen:30 probe alone must decide it, and seen:1 is never consulted.
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "茶", "ちゃ", "50")],
    )
    fake = _nested(monkeypatch, {1: {"下駄"}, 30: {"下駄"}})

    cfg = Config(priority_search=["deck:X seen:1", "deck:X seen:30"])
    dm = DataManager(cfg)
    assert [c.card_id for c in dm.get_cards_from_search("deck:X seen:1").cards] == []
    assert [c.card_id for c in dm.get_cards_from_search("deck:X seen:30").cards] == []

    assert fake.probes(30) == 1, "the largest window is probed once"
    assert fake.probes(1) == 0, "a miss at the top must short-circuit every smaller level"


def test_seen_hit_at_smaller_window_still_evaluated(fake_col, monkeypatch):
    # 下駄 is in both windows: the top probe cannot settle a hit, so seen:1 is still checked.
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "下駄", "げた", "50")],
    )
    fake = _nested(monkeypatch, {1: {"下駄"}, 30: {"下駄"}})

    cfg = Config(priority_search=["deck:X seen:1", "deck:X seen:30"])
    dm = DataManager(cfg)
    assert [c.card_id for c in dm.get_cards_from_search("deck:X seen:1").cards] == [1]
    assert fake.probes(30) == 1
    assert fake.probes(1) == 1


def test_seen_miss_at_middle_window_settles_smaller_levels(fake_col, monkeypatch):
    # The downward half of the same monotonicity: 茶 is in seen:30 but not seen:7, so it
    # cannot be in seen:1 either. Two probes must settle all three levels.
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "茶", "ちゃ", "50")],
    )
    fake = _nested(monkeypatch, {1: {"茶"}, 7: set(), 30: {"茶"}})

    cfg = Config(priority_search=["deck:X seen:1", "deck:X seen:7", "deck:X seen:30"])
    dm = DataManager(cfg)
    assert [c.card_id for c in dm.get_cards_from_search("deck:X seen:7").cards] == []
    assert [c.card_id for c in dm.get_cards_from_search("deck:X seen:1").cards] == []

    assert fake.probes(30) == 1
    assert fake.probes(7) == 1
    # The seen:1 window is deliberately stocked with 茶 so that consulting it would give the
    # WRONG answer, because nesting says a seen:7 miss is a seen:1 miss.
    assert fake.probes(1) == 0, "a miss at seen:7 must settle every smaller level"


def test_seen_short_circuit_preserves_every_answer(fake_col, monkeypatch):
    # The whole point: identical results to evaluating each window independently.
    # 下駄 seen recently, 茶 seen only in the wider window, 犬 never seen.
    fake_col(
        find_results={"(deck:X) is:new": [1, 2, 3]},
        rows=[_row(1, 10, "下駄", "げた", "50"), _row(2, 20, "茶", "ちゃ", "60"),
              _row(3, 30, "犬", "いぬ", "70")],
    )
    _nested(monkeypatch, {1: {"下駄"}, 30: {"下駄", "茶"}})

    cfg = Config(priority_search=["deck:X seen:1", "deck:X seen:30"])
    dm = DataManager(cfg)
    assert [c.card_id for c in dm.get_cards_from_search("deck:X seen:1").cards] == [1]
    assert [c.card_id for c in dm.get_cards_from_search("deck:X seen:30").cards] == [1, 2]


def test_seen_levels_share_one_reference_date(fake_col, monkeypatch):
    # Nesting only holds for windows resolved against the same 'today'; two independent
    # today_date() calls could straddle the rollover hour and break monotonicity.
    fake_col(
        find_results={"(deck:X) is:new": [1]},
        rows=[_row(1, 10, "下駄", "げた", "50")],
    )
    fake = _nested(monkeypatch, {1: {"下駄"}, 7: {"下駄"}, 30: {"下駄"}})

    cfg = Config(priority_search=["deck:X seen:1", "deck:X seen:7", "deck:X seen:30"])
    dm = DataManager(cfg)
    dm.get_cards_from_search("deck:X seen:7")

    assert len(fake.todays) == 3          # every configured level resolved together
    assert len(set(fake.todays)) == 1     # against one shared reference date
    assert fake.todays[0] is not None


# kanji:new_reading

class _ReadingKM:
    """Enough KanjiManager for the predicate: a slot counter plus the
    enable_readings handshake the predicate is required to perform."""

    def __init__(self, known=()):
        from collections import Counter
        self.known_reading_counts = Counter(known)
        self.readings_enabled = False
        self.init_calls = 0

    def enable_readings(self):
        self.readings_enabled = True

    def initialize(self):
        self.init_calls += 1

    def unresolved_reading_rate(self):
        return None


def test_new_reading_enables_the_reading_index_before_scanning(fake_col, monkeypatch):
    """enable_readings must happen before initialize(), or the scan runs once
    without slots and has to be redone."""
    fake_col(find_results={"(deck:X) is:new": [1]},
             rows=[_row(1, 10, "食事", "しょくじ", "100")])
    km = _ReadingKM()
    order = []
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: km)
    km.enable_readings = lambda: order.append("enable")
    km.initialize = lambda: order.append("init")
    dm = DataManager(Config())
    dm.get_cards_from_search("deck:X kanji:new_reading>=1")
    assert order[:2] == ["enable", "init"]


def test_new_reading_matches_only_unlearned_readings(fake_col, monkeypatch):
    import kanji_readings as kr
    # 食事 is fully learned; 食べる uses a different reading of 食.
    fake_col(
        find_results={"(deck:X) is:new": [1, 2]},
        rows=[_row(1, 10, "食事", "しょくじ", "100"),
              _row(2, 20, "食べる", "たべる", "100")],
    )
    known = dict.fromkeys(kr.reading_slots("食事", "しょくじ"), 1)
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: _ReadingKM(known))
    dm = DataManager(Config())
    result = dm.get_cards_from_search("deck:X kanji:new_reading>=1")
    assert [c.card_id for c in result.cards] == [2]


def test_new_reading_skips_cards_without_a_reading(fake_col, monkeypatch):
    fake_col(find_results={"(deck:X) is:new": [1, 2]},
             rows=[_row(1, 10, "食事", "", "100"),
                   _row(2, 20, "食べる", "たべる", "100")])
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: _ReadingKM())
    dm = DataManager(Config())
    result = dm.get_cards_from_search("deck:X kanji:new_reading>=1")
    assert [c.card_id for c in result.cards] == [2]


def test_new_reading_gets_its_own_count_cache_bucket(fake_col, monkeypatch):
    """kanji:new and kanji:new_reading count different things; sharing a cache
    bucket would let one serve the other's answers."""
    fake_col(find_results={"(deck:X) is:new": [1]},
             rows=[_row(1, 10, "食べる", "たべる", "100")])
    km = _ReadingKM()
    km.get_unknown_kanji_count = lambda text, target=1: 0
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: km)
    dm = DataManager(Config())
    dm.get_cards_from_search("deck:X kanji:new>=1")
    dm.get_cards_from_search("deck:X kanji:new_reading>=1")
    assert ("kanji", "new", 1) in dmod._term_memos
    assert ("kanji", "new_reading", 1) in dmod._term_memos


def test_unresolved_counters_are_per_note_not_per_comparison(fake_col, monkeypatch):
    """The diagnostic counts each note once, on the cache miss. Two searches
    over the same card must not double it."""
    fake_col(find_results={"(deck:X) is:new": [1], "(deck:Y) is:new": [1]},
             rows=[_row(1, 10, "火傷", "やけど", "100")])
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: _ReadingKM())
    dm = DataManager(Config())
    dm.get_cards_from_search("deck:X kanji:new_reading>=1")
    dm.get_cards_from_search("deck:Y kanji:new_reading>=1")
    assert dm._nr_total == 2          # 火 and 傷, counted once
    assert dm._nr_unresolved == 2     # jukujikun: neither is explainable


def test_reading_diagnostics_report_the_unresolved_rate(fake_col, monkeypatch):
    fake_col(find_results={"(deck:X) is:new": [1, 2]},
             rows=[_row(1, 10, "食事", "しょくじ", "100"),
                   _row(2, 20, "火傷", "やけど", "100")])
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: _ReadingKM())
    dm = DataManager(Config())
    dm.get_cards_from_search("deck:X kanji:new_reading>=0")
    assert "50% unresolved" in dm.reading_diagnostics()["new_reading_cards"]


def test_reading_diagnostics_are_empty_when_the_term_never_ran(fake_col):
    fake_col(find_results={"(deck:X) is:new": []}, rows=[])
    dm = DataManager(Config())
    dm.get_cards_from_search("deck:X")
    assert dm.reading_diagnostics() == {}


# cross-run term memos
#
# Each reorder builds a fresh DataManager, so these pin that the per-note term values carry
# over between runs and are dropped exactly when something they depend on moves.

_TWO_NOTES = [_row(1, 10, "語", "ご", "100"), _row(2, 20, "彙", "い", "100")]


def _occ_counter(calls, index):
    def fake_occurrence_counter(dict_names, **kwargs):
        def count(expression, reading, card_kanji=None):
            calls.append(expression)
            return 7
        count.index = index
        return count
    return fake_occurrence_counter


def _occ_run(fake_col, cfg=None, rows=_TWO_NOTES, note_mods=None):
    col = fake_col(find_results={"(deck:X) is:new": [1, 2]}, rows=rows)
    col.db.note_mods.update(note_mods or {})
    return DataManager(cfg or Config()).get_cards_from_search("deck:X occurrences:D>5")


def test_occ_counts_carry_over_to_the_next_run(fake_col, monkeypatch):
    calls = []
    # One index object across both runs, as the lru-cached real one would be.
    monkeypatch.setattr(dmod, "occurrence_counter", _occ_counter(calls, object()))
    _occ_run(fake_col)
    result = _occ_run(fake_col)
    assert calls == ["語", "彙"]  # the second run computed nothing
    assert [c.card_id for c in result.cards] == [1, 2]


def test_edited_note_is_recounted_alone(fake_col, monkeypatch):
    calls = []
    monkeypatch.setattr(dmod, "occurrence_counter", _occ_counter(calls, object()))
    _occ_run(fake_col)
    edited = [_row(1, 10, "新", "しん", "100"), _row(2, 20, "彙", "い", "100")]
    _occ_run(fake_col, rows=edited, note_mods={10: 2})
    assert calls == ["語", "彙", "新"]


def test_replaced_occurrence_index_recounts_everything(fake_col, monkeypatch):
    # A dictionary update clears the lru caches, so the next counter binds a new index.
    calls = []
    monkeypatch.setattr(dmod, "occurrence_counter", _occ_counter(calls, object()))
    _occ_run(fake_col)
    monkeypatch.setattr(dmod, "occurrence_counter", _occ_counter(calls, object()))
    _occ_run(fake_col)
    assert calls == ["語", "彙", "語", "彙"]


def test_flipped_matching_flag_recounts_everything(fake_col, monkeypatch):
    calls = []
    monkeypatch.setattr(dmod, "occurrence_counter", _occ_counter(calls, object()))
    _occ_run(fake_col)
    _occ_run(fake_col, cfg=Config(prefix_matching=True))
    assert calls == ["語", "彙", "語", "彙"]


def test_counter_without_an_index_memoizes_for_one_run_only(fake_col, monkeypatch):
    calls = []

    def fake_occurrence_counter(dict_names, **kwargs):
        return lambda expression, reading, card_kanji=None: calls.append(expression) or 7

    monkeypatch.setattr(dmod, "occurrence_counter", fake_occurrence_counter)
    _occ_run(fake_col)
    _occ_run(fake_col)
    assert calls == ["語", "彙", "語", "彙"]


def test_clear_note_cache_drops_the_term_memos(fake_col, monkeypatch):
    monkeypatch.setattr(dmod, "occurrence_counter", _occ_counter([], object()))
    _occ_run(fake_col)
    assert dmod._term_memos
    dmod.clear_note_cache()
    assert not dmod._term_memos


def _seen_run(fake_col, monkeypatch, window):
    fake_col(find_results={"(deck:X) is:new": [1, 2]}, rows=_TWO_NOTES)
    monkeypatch.setattr(dmod.seen_manager, "get_seen_window",
                        lambda n, k, h, v, s, c, today=None: window)
    return DataManager(Config()).get_cards_from_search("deck:X seen:3")


def test_seen_answers_carry_over_while_the_window_is_unchanged(fake_col, monkeypatch):
    window = _FakeWindow(present={"語"})
    _seen_run(fake_col, monkeypatch, window)
    result = _seen_run(fake_col, monkeypatch, window)
    assert len(window.calls) == 2  # both notes probed once, in the first run
    assert [c.card_id for c in result.cards] == [1]


def test_rewritten_seen_window_reprobes_every_note(fake_col, monkeypatch):
    # seen_manager hands back a new window object when a day's file is rewritten.
    _seen_run(fake_col, monkeypatch, _FakeWindow(present={"語"}))
    newer = _FakeWindow(present={"語", "彙"})
    result = _seen_run(fake_col, monkeypatch, newer)
    assert len(newer.calls) == 2
    assert [c.card_id for c in result.cards] == [1, 2]


class _GenKM:
    """A KanjiManager with the generation handshake, so kanji memos outlive a run."""

    def __init__(self, unknown):
        self.unknown = set(unknown)
        self.generation = 1
        self.last_delta = None
        self.calls = []

    def initialize(self):
        pass

    def get_unknown_kanji_count(self, text, target=1):
        self.calls.append(text)
        return sum(ch in self.unknown for ch in text)


def _kanji_run(fake_col, rows):
    fake_col(find_results={"(deck:X) is:new": [r[0] for r in rows]}, rows=rows)
    result = DataManager(Config()).get_cards_from_search("deck:X kanji:new>=1")
    return [c.card_id for c in result.cards]


def test_kanji_delta_recounts_only_notes_sharing_a_changed_kanji(fake_col, monkeypatch):
    rows = [_row(1, 10, "語", "ご", "100"), _row(2, 20, "彙", "い", "100"),
            _row(3, 30, "語彙", "ごい", "100")]
    km = _GenKM(unknown="語彙")
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: km)
    assert _kanji_run(fake_col, rows) == [1, 2, 3]

    # A note teaching 語 graduated in one incremental sync.
    km.unknown.discard("語")
    km.last_delta = (1, frozenset("語"), frozenset())
    km.generation = 2
    km.calls.clear()
    assert _kanji_run(fake_col, rows) == [2, 3]
    assert sorted(km.calls) == ["語", "語彙"]  # 彙 alone was kept

    # A rebuild carries no delta, so everything is recounted.
    km.last_delta = None
    km.generation = 3
    km.calls.clear()
    assert _kanji_run(fake_col, rows) == [2, 3]
    assert sorted(km.calls) == ["彙", "語", "語彙"]


def test_kanji_generation_gap_recounts_everything(fake_col, monkeypatch):
    # The delta only describes one step. Two syncs since the memo was filled cannot be
    # replayed from it.
    rows = [_row(1, 10, "語", "ご", "100"), _row(2, 20, "彙", "い", "100")]
    km = _GenKM(unknown="語彙")
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: km)
    _kanji_run(fake_col, rows)
    km.last_delta = (2, frozenset("語"), frozenset())
    km.generation = 3
    km.calls.clear()
    _kanji_run(fake_col, rows)
    assert sorted(km.calls) == ["彙", "語"]


def test_new_reading_delta_recounts_only_notes_sharing_a_changed_slot(fake_col, monkeypatch):
    import kanji_readings as kr
    rows = [_row(1, 10, "食事", "しょくじ", "100"), _row(2, 20, "火傷", "やけど", "100")]
    km = _ReadingKM()
    km.generation = 1
    km.last_delta = None
    monkeypatch.setattr(dmod, "get_kanji_manager", lambda cfg: km)

    def run():
        fake_col(find_results={"(deck:X) is:new": [1, 2]}, rows=rows)
        dm = DataManager(Config())
        cards = dm.get_cards_from_search("deck:X kanji:new_reading>=1").cards
        return [c.card_id for c in cards], dm._nr_total

    assert run() == ([1, 2], 4)
    learned = kr.reading_slots("食事", "しょくじ")
    km.known_reading_counts.update(learned)
    km.last_delta = (1, frozenset("食事"), frozenset(learned))
    km.generation = 2
    assert run() == ([2], 2)  # only 食事's two slots were re-evaluated


# in-run filter sharing

def _counting_predicates(monkeypatch):
    """Wrap every term predicate so a test can count per-card evaluations by kind."""
    evaluated = []
    original = DataManager._term_predicate

    def counting(self, kind, args):
        pred = original(self, kind, args)

        def wrapped(card):
            evaluated.append(kind)
            return pred(card)
        return wrapped

    monkeypatch.setattr(DataManager, "_term_predicate", counting)
    return evaluated


def test_searches_share_their_leading_filter_passes(fake_col, monkeypatch):
    fake_col(
        find_results={"(deck:X) is:new": [1, 2, 3]},
        rows=[_row(1, 10, "a", "r", "10"), _row(2, 20, "b", "r", "50"),
              _row(3, 30, "c", "r", "500")],
    )
    evaluated = _counting_predicates(monkeypatch)
    dm = DataManager(Config())
    first = dm.get_cards_from_search("deck:X f<100 length>=1")
    # Same terms, other order: the canonical order makes this an exact memo hit.
    second = dm.get_cards_from_search("deck:X length>=1 f<100")
    assert [c.card_id for c in first.cards] == [1, 2] == [c.card_id for c in second.cards]
    assert evaluated.count("length") == 3
    assert evaluated.count("freq") == 3


def test_shared_prefix_then_diverging_terms(fake_col, monkeypatch):
    fake_col(
        find_results={"(deck:X) is:new": [1, 2, 3]},
        rows=[_row(1, 10, "a", "r", "10"), _row(2, 20, "bb", "r", "50"),
              _row(3, 30, "c", "r", "500")],
    )
    evaluated = _counting_predicates(monkeypatch)
    dm = DataManager(Config())
    assert [c.card_id for c in dm.get_cards_from_search("deck:X length=1 f<100").cards] == [1]
    assert [c.card_id for c in dm.get_cards_from_search("deck:X length=1 f<20").cards] == [1]
    # length=1 runs over the deck once. Each freq pass sees only its two survivors.
    assert evaluated.count("length") == 3
    assert evaluated.count("freq") == 4


def test_grouped_searches_resolve_a_shared_term_once(fake_col, monkeypatch):
    fake_col(
        find_results={"(deck:X) is:new": [1, 2]},
        rows=[_row(1, 10, "語", "ご", "100"), _row(2, 20, "彙", "い", "100")],
    )
    monkeypatch.setattr(dmod.seen_manager, "get_seen_window",
                        lambda n, k, h, v, s, c, today=None: _FakeWindow(present={"語"}))
    evaluated = _counting_predicates(monkeypatch)
    dm = DataManager(Config())
    dm.get_cards_from_search("deck:X (seen:3 OR added:7) f<200")
    dm.get_cards_from_search("deck:X (seen:3 OR added:7) f<50")
    assert evaluated.count("seen") == 2  # once per candidate, not once per search
    assert evaluated.count("freq") == 4  # different thresholds, resolved separately
