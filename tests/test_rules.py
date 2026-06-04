from rules import parse_rule_string


def test_extracts_and_strips_limit():
    query, limit = parse_rule_string("deck:JP limit=20")
    assert query == "deck:JP"
    assert limit == 20


def test_no_limit_returns_none():
    query, limit = parse_rule_string("deck:JP added:3")
    assert query == "deck:JP added:3"
    assert limit is None


def test_limit_anywhere_in_string_is_removed_and_whitespace_collapsed():
    query, limit = parse_rule_string("deck:JP limit=5 -tag:done")
    assert query == "deck:JP -tag:done"
    assert limit == 5


def test_custom_terms_are_left_verbatim():
    query, limit = parse_rule_string("deck:JP occurrences:[A,B]>=5 f<2000 kanji:new=1 limit=10")
    assert query == "deck:JP occurrences:[A,B]>=5 f<2000 kanji:new=1"
    assert limit == 10


def test_whitespace_only_is_collapsed_to_empty():
    query, limit = parse_rule_string("   limit=3   ")
    assert query == ""
    assert limit == 3
