from config_manager import Config


def test_defaults_from_empty_dict():
    c = Config.from_dict({})
    assert c.priority_search == ""
    assert c.priority_search_mode == "sequential"
    assert c.sort_field == "FreqSort"
    assert c.sort_reverse is False
    assert c.priority_cutoff is None
    assert c.reorder_on_sync is True
    # matching flags are all opt-in
    assert c.combine_word_forms is False
    assert c.variant_matching is False
    assert c.prefix_matching is False
    assert c.suffix_matching is False
    assert c.stem_matching is False
    assert c.honorific_folding is False


def test_matching_flags_parse_and_reject_non_bools():
    c = Config.from_dict({"variant_matching": True, "prefix_matching": True,
                          "stem_matching": True})
    assert c.variant_matching is True
    assert c.prefix_matching is True
    assert c.stem_matching is True
    assert Config.from_dict({"variant_matching": "yes"}).variant_matching is False
    assert Config.from_dict({"stem_matching": "yes"}).stem_matching is False


def test_invalid_mode_falls_back_to_sequential():
    assert Config.from_dict({"priority_search_mode": "turbo"}).priority_search_mode == "sequential"


def test_bad_types_are_coerced_to_defaults():
    c = Config.from_dict({"sort_reverse": "yes", "sort_field": 123})
    assert c.sort_reverse is False
    assert c.sort_field == "FreqSort"


def test_priority_search_list_filters_non_strings():
    c = Config.from_dict({"priority_search": ["a", 5, "b", None]})
    assert c.priority_search == ["a", "b"]


def test_priority_search_invalid_type_becomes_empty_string():
    assert Config.from_dict({"priority_search": 42}).priority_search == ""


def test_optional_int_accepts_int_numeric_str_and_null():
    assert Config.from_dict({"priority_cutoff": 7}).priority_cutoff == 7
    assert Config.from_dict({"priority_cutoff": "9"}).priority_cutoff == 9
    assert Config.from_dict({"priority_cutoff": None}).priority_cutoff is None


def test_optional_int_rejects_bool():
    # bool is an int subclass; must not be silently accepted as a threshold.
    assert Config.from_dict({"priority_cutoff": True}).priority_cutoff is None


def test_reorder_on_sync_fallback_chain():
    assert Config.from_dict({"reorder_on_sync": False}).reorder_on_sync is False
    assert Config.from_dict({"reorder_after_sync": False}).reorder_on_sync is False  # legacy key
    assert Config.from_dict({"reorder_before_sync": False}).reorder_on_sync is False  # older key
    assert Config.from_dict({}).reorder_on_sync is True


def test_search_fields_nested_config():
    c = Config.from_dict({"search_fields": {
        "expression_field": "Word",
        "expression_reading_field": "Kana",
    }})
    assert c.search_config.expression_field == "Word"
    assert c.search_config.expression_reading_field == "Kana"
