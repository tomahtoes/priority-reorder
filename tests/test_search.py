import sys
import types

import pytest

import search


def occ_recorder(calls, ids=None):
    def resolve(dict_str, op, thresh):
        calls.append((dict_str, op, thresh))
        return ids if ids is not None else [101, 202]
    return resolve


def freq_recorder(calls, ids=None):
    def resolve(op, thresh):
        calls.append((op, thresh))
        return ids if ids is not None else [11, 22]
    return resolve


def kanji_recorder(calls, ids=None):
    def resolve(check_type, target, op, thresh):
        calls.append((check_type, target, op, thresh))
        return ids if ids is not None else [33, 44]
    return resolve


def length_recorder(calls, ids=None):
    def resolve(op, thresh):
        calls.append((op, thresh))
        return ids if ids is not None else [55, 66]
    return resolve


# fast path / passthrough

def test_no_custom_token_is_passthrough():
    calls = []
    out = search.rewrite_query("deck:Mining added:3", occ_resolver=occ_recorder(calls))
    assert out == "deck:Mining added:3"
    assert calls == []  # resolver never invoked


def test_empty_query_passthrough():
    assert search.rewrite_query("", occ_resolver=occ_recorder([])) == ""


# occurrences

def test_occurrences_single_dict():
    calls = []
    out = search.rewrite_query("occurrences:MyDict>=5", occ_resolver=occ_recorder(calls))
    assert out == "(nid:101,202)"
    assert calls == [("MyDict", ">=", 5)]


def test_occurrences_bracketed_combinator():
    calls = []
    out = search.rewrite_query("occurrences:[A,B,C]>10", occ_resolver=occ_recorder(calls))
    assert out == "(nid:101,202)"
    assert calls == [("[A,B,C]", ">", 10)]


def test_occurrences_all_keyword():
    calls = []
    search.rewrite_query("occurrences:all>5", occ_resolver=occ_recorder(calls))
    assert calls == [("all", ">", 5)]


def test_occurrences_empty_result_is_nid_zero():
    out = search.rewrite_query("occurrences:X>5", occ_resolver=lambda d, o, t: [])
    assert out == "nid:0"


def test_occurrences_negation_preserved():
    out = search.rewrite_query("-occurrences:X>5", occ_resolver=occ_recorder([]))
    assert out == "-(nid:101,202)"  # leading '-' stays, group negated


def test_occurrences_combined_with_other_clauses():
    out = search.rewrite_query(
        "deck:JP occurrences:X>5 -tag:done", occ_resolver=occ_recorder([])
    )
    assert out == "deck:JP (nid:101,202) -tag:done"


def test_occurrences_fires_inside_parentheses():
    out = search.rewrite_query("(occurrences:X>5)", occ_resolver=occ_recorder([]))
    assert out == "((nid:101,202))"


def test_occurrences_does_not_fire_inside_other_tokens():
    calls = []
    assert (
        search.rewrite_query("deck:occurrences:X>5", occ_resolver=occ_recorder(calls))
        == "deck:occurrences:X>5"
    )
    assert calls == []


def test_occurrences_all_operators():
    for op in ("<", "<=", ">", ">=", "=", "!="):
        calls = []
        search.rewrite_query(f"occurrences:D{op}4", occ_resolver=occ_recorder(calls))
        assert calls == [("D", op, 4)]


# frequency

def test_frequency_basic():
    calls = []
    out = search.rewrite_query("f<10000", freq_resolver=freq_recorder(calls))
    assert out == "(nid:11,22)"
    assert calls == [("<", 10000)]


def test_frequency_does_not_fire_inside_field_tokens():
    calls = []
    # `flag:1` and `front:x` begin with 'f' but are not followed by an operator.
    assert search.rewrite_query("flag:1", freq_resolver=freq_recorder(calls)) == "flag:1"
    assert search.rewrite_query("Front:foo", freq_resolver=freq_recorder(calls)) == "Front:foo"
    assert calls == []


def test_frequency_negation_preserved():
    out = search.rewrite_query("-f>=2000", freq_resolver=freq_recorder([]))
    assert out == "-(nid:11,22)"


# kanji

def test_kanji_new():
    calls = []
    out = search.rewrite_query("kanji:new=1", kanji_resolver=kanji_recorder(calls))
    assert out == "(nid:33,44)"
    assert calls == [("new", 1, "=", 1)]  # bracketless -> default target 1


def test_kanji_num():
    calls = []
    search.rewrite_query("kanji:num>=2", kanji_resolver=kanji_recorder(calls))
    assert calls == [("num", 1, ">=", 2)]


def test_kanji_new_bracketed_target():
    calls = []
    out = search.rewrite_query("kanji:new[3]>=1", kanji_resolver=kanji_recorder(calls))
    assert out == "(nid:33,44)"
    assert calls == [("new", 3, ">=", 1)]


def test_kanji_new_zero_target_parses():
    # Degenerate but consistent (count < 0 is never true), like seen:0.
    calls = []
    search.rewrite_query("kanji:new[0]<=0", kanji_resolver=kanji_recorder(calls))
    assert calls == [("new", 0, "<=", 0)]


def test_kanji_bracketed_negation_preserved():
    out = search.rewrite_query("-kanji:new[3]>=1", kanji_resolver=kanji_recorder([]))
    assert out == "-(nid:33,44)"


def test_kanji_num_rejects_bracket():
    # The target bracket is only meaningful for `new`; `num[T]` must not match
    # and falls through to Anki's backend like any other malformed term.
    calls = []
    out = search.rewrite_query("kanji:num[3]>=1", kanji_resolver=kanji_recorder(calls))
    assert out == "kanji:num[3]>=1"
    assert calls == []


def test_kanji_new_empty_bracket_not_matched():
    calls = []
    out = search.rewrite_query("kanji:new[]>=1", kanji_resolver=kanji_recorder(calls))
    assert out == "kanji:new[]>=1"
    assert calls == []


# length

def test_length_basic():
    calls = []
    out = search.rewrite_query("length>=3", length_resolver=length_recorder(calls))
    assert out == "(nid:55,66)"
    assert calls == [(">=", 3)]


def test_length_all_operators():
    for op in ("<", "<=", ">", ">=", "=", "!="):
        calls = []
        search.rewrite_query(f"length{op}2", length_resolver=length_recorder(calls))
        assert calls == [(op, 2)]


def test_length_negation_preserved():
    out = search.rewrite_query("-length>=4", length_resolver=length_recorder([]))
    assert out == "-(nid:55,66)"


def test_length_does_not_fire_inside_other_tokens():
    calls = []
    # `wavelength>=3` embeds the keyword; `length:5` is a field search (no operator).
    assert search.rewrite_query("wavelength>=3", length_resolver=length_recorder(calls)) == "wavelength>=3"
    assert search.rewrite_query("length:5", length_resolver=length_recorder(calls)) == "length:5"
    assert calls == []


def test_length_combined_with_other_clauses():
    out = search.rewrite_query(
        "deck:JP length=1 -tag:done", length_resolver=length_recorder([])
    )
    assert out == "deck:JP (nid:55,66) -tag:done"


# multiple terms in one query

def test_multiple_distinct_terms():
    out = search.rewrite_query(
        "occurrences:X>5 kanji:new=1 f<2000 length>=2",
        occ_resolver=occ_recorder([], ids=[1]),
        kanji_resolver=kanji_recorder([], ids=[2]),
        freq_resolver=freq_recorder([], ids=[3]),
        length_resolver=length_recorder([], ids=[4]),
    )
    assert out == "(nid:1) (nid:2) (nid:3) (nid:4)"


# resolution cache invalidation

def test_resolution_cache_invalidated_on_config_change(monkeypatch):
    # Editing addon config doesn't bump mw.col.mod, so the memo signature must
    # include a config fingerprint or stale nid sets get served.
    import config_manager
    from config_manager import Config

    aqt = sys.modules["aqt"]
    monkeypatch.setattr(aqt.mw, "col", types.SimpleNamespace(mod=1), raising=False)

    cfgs = {"cur": Config()}
    monkeypatch.setattr(config_manager, "get_config", lambda: cfgs["cur"])
    monkeypatch.setattr(search, "_resolution_cache", {}, raising=False)
    monkeypatch.setattr(search, "_resolution_sig", None, raising=False)

    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return [1]

    key = ("occ", "D", ">", 5)
    assert search._resolve(key, compute, None) == [1]
    assert search._resolve(key, compute, None) == [1]
    assert calls["n"] == 1  # same collection + same config -> memoized

    cfgs["cur"] = Config(prefix_matching=True)  # config edit, col.mod unchanged
    assert search._resolve(key, compute, None) == [1]
    assert calls["n"] == 2  # recomputed

    # every match-affecting flag must be in the fingerprint, including variant_matching
    cfgs["cur"] = Config(prefix_matching=True, variant_matching=True)
    assert search._resolve(key, compute, None) == [1]
    assert calls["n"] == 3


# has_custom_term

def test_has_custom_term():
    assert search.has_custom_term("occurrences:X>5")
    assert search.has_custom_term("deck:JP f<=10")
    assert search.has_custom_term("kanji:num=2")
    assert search.has_custom_term("kanji:new[3]>=1")
    assert search.has_custom_term("length>=3")
    assert search.has_custom_term("deck:JP length=1")
    assert not search.has_custom_term("kanji:num[3]>=1")  # bracket is new-only
    assert not search.has_custom_term("wavelength>=3")
    assert not search.has_custom_term("length:5")
    assert not search.has_custom_term("deck:JP added:3 flag:1")
    assert not search.has_custom_term("")


# kanji:new_reading grammar

@pytest.mark.parametrize("query,expected", [
    ("kanji:new_reading>=1", ("new_reading", 1, ">=", 1)),
    ("kanji:new_reading[3]>=2", ("new_reading", 3, ">=", 2)),
    ("kanji:new_reading<=0", ("new_reading", 1, "<=", 0)),
])
def test_new_reading_parses_with_and_without_a_target(query, expected):
    assert search.parse_custom_terms(query) == [("kanji", expected, False)]


def test_new_reading_negation_is_detected():
    assert search.parse_custom_terms("-kanji:new_reading>=1") == [
        ("kanji", ("new_reading", 1, ">=", 1), True)]


@pytest.mark.parametrize("query,expected", [
    ("kanji:new>=1", [("kanji", ("new", 1, ">=", 1), False)]),
    ("kanji:new[3]>=1", [("kanji", ("new", 3, ">=", 1), False)]),
    ("kanji:num>=2", [("kanji", ("num", 1, ">=", 2), False)]),
    ("kanji:num[2]>=1", []),          # a bracket on num is still not a term
])
def test_existing_kanji_grammar_is_unchanged(query, expected):
    """Adding new_reading to the alternation and flipping the bracket guard from
    (?<=new) to (?<!num) may not disturb what the original two types matched."""
    assert search.parse_custom_terms(query) == expected


def test_new_reading_is_stripped_from_the_anki_query():
    assert "new_reading" not in search._strip_custom_terms("deck:x kanji:new_reading[2]>=1")


def test_new_reading_counts_as_a_custom_term():
    assert search.has_custom_term("kanji:new_reading>=1")


# first-letter aliases


def seen_recorder(calls, ids=None):
    def resolve(n):
        calls.append(n)
        return ids if ids is not None else [7, 8]
    return resolve


def _rewrite_all(query, calls):
    """rewrite_query with every resolver recording into one shared list, so a long form
    and its alias can be compared on output and on resolver arguments at the same time."""
    return search.rewrite_query(
        query,
        occ_resolver=occ_recorder(calls),
        freq_resolver=freq_recorder(calls),
        kanji_resolver=kanji_recorder(calls),
        seen_resolver=seen_recorder(calls),
        length_resolver=length_recorder(calls),
    )


@pytest.mark.parametrize("long_form,alias", [
    ("occurrences:MyDict>=5", "o:MyDict>=5"),
    ("occurrences:[A,B,C]>10", "o:[A,B,C]>10"),
    ("occurrences:all>5", "o:all>5"),
    ("kanji:new>=1", "k:new>=1"),
    ("kanji:new[3]>=1", "k:new[3]>=1"),
    ("kanji:new_reading[3]>=2", "k:new_reading[3]>=2"),
    ("kanji:num>=2", "k:num>=2"),
    ("seen:7", "s:7"),
    ("seen:0", "s:0"),
    ("length>=3", "l>=3"),
    ("-occurrences:E<1", "-o:E<1"),
    ("(seen:5)", "(s:5)"),
    ("-length=1", "-l=1"),
    ("deck:JP occurrences:D>5 kanji:new>=1 f<2000 length>=2 seen:7 -tag:done",
     "deck:JP o:D>5 k:new>=1 f<2000 l>=2 s:7 -tag:done"),
])
def test_alias_resolves_exactly_like_the_long_form(long_form, alias):
    long_calls, alias_calls = [], []
    assert _rewrite_all(alias, alias_calls) == _rewrite_all(long_form, long_calls)
    assert alias_calls == long_calls


@pytest.mark.parametrize("query", [
    # Anki's own keys, including the short ones it already reserves.
    "sc:5", "cds:5", "nc:foo", "re:x", "w:foo", "is:new", "flag:1", "prop:s>1", "added:3",
    # A field search named after an alias letter, or after that letter plus more.
    "o:foo", "s:foo", "k:foo", "l:5", "length:5", "on:foo>=1", "no:x>1", "nk:new>=1",
    # The alias sitting inside a larger token.
    "deck:s:5", "deck:o:X>5", "unseen:5", "wavelength>=3",
    # limit= is a reorder control (see rules.py), never a length term.
    "limit=5",
])
def test_alias_leaves_anki_syntax_and_field_searches_alone(query):
    calls = []
    assert _rewrite_all(query, calls) == query
    assert calls == []


def test_has_custom_term_accepts_aliases():
    assert search.has_custom_term("o:X>5")
    assert search.has_custom_term("k:num=2")
    assert search.has_custom_term("s:2")
    assert search.has_custom_term("deck:JP l=1")
    assert not search.has_custom_term("l:5")
    assert not search.has_custom_term("limit=5")
    assert not search.has_custom_term("sc:5")


def test_aliases_strip_to_the_standard_anki_part():
    q = "deck:JP o:D>5 k:new>=1 f<2000 l>=2 s:7 -o:E<1"
    assert search._strip_custom_terms(q).split() == ["deck:JP", "-"]


def test_rewriting_an_alias_query_twice_changes_nothing():
    # No pattern may fire inside the (nid:...) clause it just produced.
    once = _rewrite_all("deck:JP s:7 o:D>5 k:new>=1 l>=2", [])
    assert _rewrite_all(once, []) == once


def test_candidate_restriction_recognizes_aliases():
    assert search._candidate_restriction_allowed("deck:JP s:7 o:D>5", "deck:JP")
    assert not search._candidate_restriction_allowed("deck:JP or s:7", "deck:JP or")
    # Inside quotes the optimization has to bail; a quote directly before the alias is a
    # different case, blocked earlier by the lookbehind.
    assert not search._candidate_restriction_allowed('tag:"a s:7 b" deck:JP', "deck:JP")
