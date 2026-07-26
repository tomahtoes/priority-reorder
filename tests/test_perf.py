"""Regression guard for the custom-term resolution performance fix.

Commit dea7b19 made every `occurrences:`/`f`/`kanji:` term resolve by scanning the
*whole collection* (O(M)) instead of only the notes the rest of the query already
matched (O(N), as the old rule-based path did). These tests pin the restored
behaviour so it can't silently regress:

  * `_iter_candidate_notes` must fetch ONLY the candidate notes when given a set,
    not run a per-note-type full scan;
  * `rewrite_query` must compute the candidate set from the standard part of a
    conjunctive query and thread it into the resolver, and must fall back to a
    full scan (candidate=None) for unsafe queries (top-level disjunction, bare
    custom term, custom term inside a group).

They also include a micro-benchmark demonstrating the O(M) vs O(N) gap with real
occurrence-index lookups (run with `-s` to see the timings).
"""

import sys
import time
import types

import pytest

import search
from dictionary_manager import OccurrenceIndex


# --- fake collection harness ------------------------------------------------

class _FakeModels:
    """Single note type (mid=1) whose fields are Expression(0)/Reading(1)/Freq(2)."""

    _FIELDS = {"Expression": 0, "Reading": 1, "Freq": 2}

    def all(self):
        return [{"id": 1, "name": "Basic"}]

    def field_map(self, model):
        return {name: (ord_, {"name": name}) for name, ord_ in self._FIELDS.items()}


class _FakeDB:
    def __init__(self, notes):
        # notes: list of (nid, mid, flds)
        self.notes = notes
        self.executed = []

    def execute(self, sql, *params):
        self.executed.append((sql, params))
        if "where mid = ?" in sql:                      # full-scan per note type
            mid = params[0]
            return [(nid, flds) for (nid, m, flds) in self.notes if m == mid]
        if "where id in" in sql:                        # restricted single pass
            inside = sql[sql.index("(") + 1 : sql.rindex(")")]
            ids = {int(x) for x in inside.split(",") if x.strip()}
            return [(nid, m, flds) for (nid, m, flds) in self.notes if nid in ids]
        return []


class _FakeCol:
    def __init__(self, notes, find_notes_result):
        self.mod = 12345
        self.models = _FakeModels()
        self.db = _FakeDB(notes)
        self._find_notes_result = find_notes_result
        self.find_notes_queries = []

    def find_notes(self, query):
        self.find_notes_queries.append(query)
        return list(self._find_notes_result)


def _flds(expr="", reading="", freq=""):
    return "\x1f".join([expr, reading, freq])


@pytest.fixture
def fake_anki(monkeypatch):
    """Install fake `aqt` (+ `mw`) and `anki.utils` modules and return a builder
    that wires a fake collection onto them."""
    aqt = types.ModuleType("aqt")
    aqt.mw = types.SimpleNamespace(col=None)
    monkeypatch.setitem(sys.modules, "aqt", aqt)

    anki = types.ModuleType("anki")
    anki_utils = types.ModuleType("anki.utils")
    anki_utils.ids2str = lambda ids: "(" + ",".join(str(i) for i in ids) + ")"
    monkeypatch.setitem(sys.modules, "anki", anki)
    monkeypatch.setitem(sys.modules, "anki.utils", anki_utils)

    # install() is never called in tests, so the saved original stays None and
    # _default_find_notes falls back to the live (fake) mw.col.find_notes.
    monkeypatch.setattr(search, "_original_find_notes", None, raising=False)

    def build(notes, find_notes_result=()):
        col = _FakeCol(notes, find_notes_result)
        aqt.mw.col = col
        return col

    return build


# --- _iter_candidate_notes: the O(M) vs O(N) guard --------------------------

def test_iter_candidate_notes_full_scan_visits_everything(fake_anki):
    notes = [(i, 1, _flds(f"e{i}", f"r{i}")) for i in range(100)]
    fake_anki(notes)

    visited = list(search._iter_candidate_notes(("Expression", "Reading")))

    assert len(visited) == 100


def test_iter_candidate_notes_restricted_visits_only_candidates(fake_anki):
    notes = [(i, 1, _flds(f"e{i}", f"r{i}")) for i in range(100)]
    col = fake_anki(notes)

    candidates = {3, 7, 42}
    visited = list(search._iter_candidate_notes(("Expression", "Reading"), candidates))

    assert {nid for nid, _ in visited} == candidates
    # The whole point: NO per-note-type full scan, exactly one targeted fetch.
    assert all("where mid = ?" not in sql for sql, _ in col.db.executed)
    assert sum("where id in" in sql for sql, _ in col.db.executed) == 1


def test_iter_candidate_notes_empty_candidate_set_does_nothing(fake_anki):
    col = fake_anki([(1, 1, _flds("e", "r"))])
    assert list(search._iter_candidate_notes(("Expression", "Reading"), set())) == []
    assert col.db.executed == []  # no query at all


# --- rewrite_query: candidate-set threading + safety fallback ---------------

def _record_occ(record, ids=(901, 902)):
    def fake(dict_str, op, thresh, candidate_nids=None):
        record.append(candidate_nids)
        return list(ids)
    return fake


def test_rewrite_restricts_conjunctive_query_to_standard_part(fake_anki, monkeypatch):
    col = fake_anki(notes=[], find_notes_result=[101, 102])
    record = []
    monkeypatch.setattr(search, "resolve_occurrences", _record_occ(record))

    out = search.rewrite_query("deck:X occurrences:MyDict>5")

    assert out == "deck:X (nid:901,902)"
    assert record == [{101, 102}]               # resolver got the candidate set
    assert col.find_notes_queries == ["deck:X"]  # computed from the stripped query


def test_rewrite_negated_custom_term_strips_dangling_dash(fake_anki, monkeypatch):
    col = fake_anki(notes=[], find_notes_result=[5])
    record = []
    monkeypatch.setattr(search, "resolve_occurrences", _record_occ(record))

    out = search.rewrite_query("deck:X -occurrences:MyDict>5")

    assert out == "deck:X -(nid:901,902)"        # negation preserved
    assert record == [{5}]
    assert col.find_notes_queries == ["deck:X"]  # stray '-' dropped before find_notes


@pytest.mark.parametrize("query", [
    "occurrences:MyDict>5",              # bare custom term, no standard part
    "deck:A OR occurrences:MyDict>5",    # top-level disjunction
    "(deck:A occurrences:MyDict>5)",     # custom term inside a group
])
def test_rewrite_unsafe_queries_fall_back_to_full_scan(fake_anki, monkeypatch, query):
    col = fake_anki(notes=[], find_notes_result=[1, 2, 3])
    record = []
    monkeypatch.setattr(search, "resolve_occurrences", _record_occ(record))

    search.rewrite_query(query)

    assert record == [None]                # full scan (no candidate restriction)
    assert col.find_notes_queries == []    # never narrowed the candidate set


def test_rewrite_grouped_conjunctive_query_still_restricts(fake_anki, monkeypatch):
    # A group at the top level is one standard conjunct; the custom term outside
    # it applies conjunctively, so restriction to the group's matches is safe.
    col = fake_anki(notes=[], find_notes_result=[11, 12])
    record = []
    monkeypatch.setattr(search, "resolve_occurrences", _record_occ(record))

    out = search.rewrite_query("(deck:A or deck:B) occurrences:MyDict>5")

    assert out == "(deck:A or deck:B) (nid:901,902)"
    assert record == [{11, 12}]
    assert col.find_notes_queries == ["(deck:A or deck:B)"]


# --- pure helpers -----------------------------------------------------------

def test_strip_custom_terms_leaves_standard_part():
    assert search._strip_custom_terms("deck:X occurrences:D>5 f<2000 kanji:new=1 seen:2 length>=3").split() == ["deck:X"]
    assert search._strip_custom_terms("deck:X kanji:new[3]>=1").split() == ["deck:X"]


@pytest.mark.parametrize("query,stripped,allowed", [
    ("deck:X occurrences:D>5", "deck:X", True),
    ("deck:X -occurrences:D>5", "deck:X -", True),
    ("deck:X seen:3", "deck:X", True),
    ("deck:X length>=3", "deck:X", True),
    ("(deck:A length>=3)", "(deck:A )", False),
    ("occurrences:D>5", "", False),
    ("-occurrences:D>5", "-", False),
    ("deck:A or occurrences:D>5", "deck:A or", False),
    ("deck:A OR occurrences:D>5", "deck:A OR", False),
    # Grouped standard parts are fine as long as every custom term sits at the
    # top level: the group is one conjunct of a top-level conjunction.
    ("(deck:A) occurrences:D>5", "(deck:A)", True),
    ("(deck:A or deck:B) occurrences:D>5", "(deck:A or deck:B)", True),
    ("(deck:A or deck:B) is:new seen:3", "(deck:A or deck:B) is:new", True),
    ('deck:"A (B)" f<10', 'deck:"A (B)"', True),      # quoted parens don't count
    ('deck:"a or b" f<10', 'deck:"a or b"', True),    # quoted or isn't an operator
    ("wordor f<10", "wordor", True),                  # 'or' inside a word isn't either
    # Custom term inside a group / top-level disjunction / anomalies -> full scan.
    ("(deck:A occurrences:D>5)", "(deck:A )", False),
    ("-(occurrences:D>5) deck:A", "-( ) deck:A", False),
    ("(deck:A occurrences:D>5", "(deck:A", False),    # unbalanced paren
    ('deck:"A f<10', 'deck:"A', False),               # unterminated quote
])
def test_candidate_restriction_allowed(query, stripped, allowed):
    assert search._candidate_restriction_allowed(query, stripped) is allowed


# --- parse_custom_terms (reorder post-filter parser) ------------------------

def test_parse_custom_terms_extracts_each_kind():
    terms = search.parse_custom_terms("deck:X occurrences:MyDict>=5 f<2000 kanji:new=1 kanji:new[3]>=1 seen:2 length>=3")
    assert ("occ", ("MyDict", ">=", 5), False) in terms
    assert ("freq", ("<", 2000), False) in terms
    assert ("kanji", ("new", 1, "=", 1), False) in terms
    assert ("kanji", ("new", 3, ">=", 1), False) in terms
    assert ("seen", (2,), False) in terms
    assert ("length", (">=", 3), False) in terms


def test_parse_custom_terms_flags_negation():
    terms = search.parse_custom_terms("deck:X -occurrences:MyDict>=5 occurrences:Other>1")
    by_dict = {args[0]: negated for kind, args, negated in terms if kind == "occ"}
    assert by_dict == {"MyDict": True, "Other": False}


def test_parse_custom_terms_none_when_plain():
    assert search.parse_custom_terms("deck:X added:3") == []


# --- micro-benchmark: demonstrates and guards O(M) vs O(N) ------------------

def _bench_index(k_terms):
    idx = OccurrenceIndex()
    for i in range(k_terms):
        idx.add(f"語{i:05d}", f"ご{i:05d}", (i % 50) + 1)
    return idx


def test_resolution_is_linear_in_candidates_not_collection(capsys):
    """The per-note predicate runs exactly once per *visited* note, so restricting
    to a candidate subset is O(N) where the full scan is O(M). Asserted via a call
    counter (deterministic); wall-times printed for information only."""
    M = 20_000          # whole-collection size
    N = M // 100        # candidate subset (what a deck/tag filter leaves)
    idx = _bench_index(5_000)
    notes = [(f"語{i % 5000:05d}", f"ご{i % 5000:05d}") for i in range(M)]

    calls = {"n": 0}

    def predicate(expr, reading):
        calls["n"] += 1
        return idx.get_total(expr, reading, prefix_matching=True) >= 10

    t0 = time.perf_counter()
    for expr, reading in notes:                 # simulates the old full scan
        predicate(expr, reading)
    full_ms = (time.perf_counter() - t0) * 1000
    full_calls = calls["n"]

    calls["n"] = 0
    t0 = time.perf_counter()
    for expr, reading in notes[:N]:             # simulates restricted resolution
        predicate(expr, reading)
    restricted_ms = (time.perf_counter() - t0) * 1000
    restricted_calls = calls["n"]

    assert full_calls == M
    assert restricted_calls == N                # 100x fewer predicate evaluations

    with capsys.disabled():
        print(
            f"\n[perf] full scan: {full_ms:.1f} ms over {M} notes | "
            f"restricted: {restricted_ms:.1f} ms over {N} notes "
            f"({full_ms / max(restricted_ms, 1e-9):.0f}x)"
        )


def _suffix_bench_index(k_terms):
    idx = OccurrenceIndex()
    for i in range(k_terms):
        idx.add(f"{i:05d}学校", f"がっこう{i:05d}", (i % 50) + 1)  # all share the eligible tail 学校
    return idx


def test_suffix_total_is_logarithmic_not_linear(capsys):
    """suffix_total is O(log n) (two bisects + one cumsum subtraction) even when the matched
    suffix range is the entire index — asserted by matching a naive endswith scan and showing
    the speedup. The tail is a multi-char (≥2, kanji) string so it passes the eligibility gate;
    a bare single kanji is gated out and early-returns 0 (pinned below). Suffix mirror of the
    prefix_matching lookup benchmark."""
    K = 20_000
    idx = _suffix_bench_index(K)

    def naive():
        return sum(c for e, c in idx.expr_to_count.items() if e.endswith("学校") and e != "学校")

    naive_total = naive()
    idx.suffix_total("学校")  # warm the lazy reversed index once (real steady state)

    t0 = time.perf_counter()
    for _ in range(1000):
        fast_total = idx.suffix_total("学校")
    fast_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for _ in range(1000):
        scan_total = naive()
    naive_ms = (time.perf_counter() - t0) * 1000

    assert fast_total == naive_total == scan_total
    assert idx.suffix_total("語") == 0   # single kanji is gated out of the bare path (early return)
    # the log-n lookup must be dramatically faster than the O(K) scan over 20k terms
    assert fast_ms * 10 < naive_ms

    with capsys.disabled():
        print(
            f"\n[perf] suffix_total x1000: {fast_ms:.1f} ms (bisect+cumsum) | "
            f"naive endswith scan x1000: {naive_ms:.1f} ms over {K} terms "
            f"({naive_ms / max(fast_ms, 1e-9):.0f}x)"
        )
