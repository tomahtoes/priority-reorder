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
    def resolve(check_type, op, thresh):
        calls.append((check_type, op, thresh))
        return ids if ids is not None else [33, 44]
    return resolve


# --- fast path / passthrough ------------------------------------------------

def test_no_custom_token_is_passthrough():
    calls = []
    out = search.rewrite_query("deck:Mining added:3", occ_resolver=occ_recorder(calls))
    assert out == "deck:Mining added:3"
    assert calls == []  # resolver never invoked


def test_empty_query_passthrough():
    assert search.rewrite_query("", occ_resolver=occ_recorder([])) == ""


# --- occurrences ------------------------------------------------------------

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


# --- frequency --------------------------------------------------------------

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


# --- kanji ------------------------------------------------------------------

def test_kanji_new():
    calls = []
    out = search.rewrite_query("kanji:new=1", kanji_resolver=kanji_recorder(calls))
    assert out == "(nid:33,44)"
    assert calls == [("new", "=", 1)]


def test_kanji_num():
    calls = []
    search.rewrite_query("kanji:num>=2", kanji_resolver=kanji_recorder(calls))
    assert calls == [("num", ">=", 2)]


# --- multiple terms in one query -------------------------------------------

def test_multiple_distinct_terms():
    out = search.rewrite_query(
        "occurrences:X>5 kanji:new=1 f<2000",
        occ_resolver=occ_recorder([], ids=[1]),
        kanji_resolver=kanji_recorder([], ids=[2]),
        freq_resolver=freq_recorder([], ids=[3]),
    )
    assert out == "(nid:1) (nid:2) (nid:3)"


# --- has_custom_term --------------------------------------------------------

def test_has_custom_term():
    assert search.has_custom_term("occurrences:X>5")
    assert search.has_custom_term("deck:JP f<=10")
    assert search.has_custom_term("kanji:num=2")
    assert not search.has_custom_term("deck:JP added:3 flag:1")
    assert not search.has_custom_term("")
