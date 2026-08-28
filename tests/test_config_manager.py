from config_manager import Config, migrate_config


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


# --- back-compat: pre-section flat configs -----------------------------------
# The tests above all pass flat dicts and must keep passing untouched — that is the
# back-compat guarantee stated as a test.


def test_flat_config_migrates_into_sections():
    migrated, changed = migrate_config({
        "sort_field": "Freq",
        "prefix_matching": True,
        "priority_limit": 200,
        "shift_existing": False,
        "auto_update_dicts": True,
    })
    assert changed is True
    assert migrated["matching"]["prefix_matching"] is True
    assert migrated["queue_rules"]["priority_limit"] == 200
    assert migrated["queue_rules"]["shift_existing"] is False
    assert migrated["automation"]["auto_update_dicts"] is True
    # flat originals are gone, ungrouped keys are left alone
    assert "prefix_matching" not in migrated
    assert "priority_limit" not in migrated
    assert migrated["sort_field"] == "Freq"


def test_flat_config_still_parses():
    c = Config.from_dict({"prefix_matching": True, "priority_limit": 200,
                          "shift_existing": False, "auto_update_dicts": True})
    assert c.prefix_matching is True
    assert c.priority_limit == 200
    assert c.shift_existing is False
    assert c.auto_update_dicts is True


def test_sectioned_config_parses():
    c = Config.from_dict({
        "matching": {"variant_matching": True, "stem_matching": True},
        "queue_rules": {"priority_cutoff": 5000},
        "automation": {"reorder_on_sync": False},
    })
    assert c.variant_matching is True
    assert c.stem_matching is True
    assert c.priority_cutoff == 5000
    assert c.reorder_on_sync is False


def test_flat_key_wins_over_the_section_beside_it():
    # This is the shape getConfig actually hands us for an unmigrated user: Anki
    # shallow-merges config.json, so the default sections sit alongside the user's
    # real flat values. A flat key present at all means the config predates the
    # sections (migration always removes them), so it must win — otherwise every
    # upgrading user silently gets reset to defaults.
    migrated, _ = migrate_config({"prefix_matching": True,
                                  "matching": {"prefix_matching": False,
                                               "variant_matching": True}})
    assert migrated["matching"]["prefix_matching"] is True
    assert migrated["matching"]["variant_matching"] is True  # untouched by any flat key
    assert "prefix_matching" not in migrated
    assert Config.from_dict({"prefix_matching": True,
                             "matching": {"prefix_matching": False}}).prefix_matching is True


def test_shipped_defaults_merged_over_a_flat_config_keep_user_values():
    # Full regression for the upgrade path, using the real shipped defaults.
    import json, os
    defaults = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json"),
                              encoding="utf-8"))
    stored = {"priority_limit": 200, "shift_existing": False, "prefix_matching": True,
              "reorder_after_sync": False, "sort_field": "Freq"}
    merged = dict(defaults)
    merged.update(stored)  # exactly what Anki's getConfig does
    c = Config.from_dict(merged)
    assert c.priority_limit == 200
    assert c.shift_existing is False
    assert c.prefix_matching is True
    assert c.reorder_on_sync is False
    assert c.sort_field == "Freq"


def test_legacy_sync_aliases_land_in_automation():
    assert migrate_config({"reorder_after_sync": False})[0]["automation"]["reorder_on_sync"] is False
    assert migrate_config({"reorder_before_sync": False})[0]["automation"]["reorder_on_sync"] is False
    # precedence is unchanged: the canonical key beats the older spellings
    migrated, _ = migrate_config({"reorder_on_sync": True, "reorder_after_sync": False,
                                  "reorder_before_sync": False})
    assert migrated["automation"]["reorder_on_sync"] is True
    assert "reorder_after_sync" not in migrated and "reorder_before_sync" not in migrated


def test_migration_is_idempotent():
    once, _ = migrate_config({"prefix_matching": True, "reorder_after_sync": False})
    twice, changed = migrate_config(once)
    assert changed is False
    assert twice == once


def test_partial_section_is_backfilled_with_defaults():
    # Anki's shallow merge replaces a saved section wholesale, so options added in
    # later versions must be filled in here or they never reach the editor.
    migrated, changed = migrate_config({"matching": {"prefix_matching": True}})
    assert changed is True
    assert migrated["matching"]["prefix_matching"] is True
    assert migrated["matching"]["variant_matching"] is False
    assert set(migrated["matching"]) == {
        "kana_normalization", "combine_word_forms", "variant_matching", "stem_matching",
        "prefix_matching", "suffix_matching", "honorific_folding",
    }


def test_garbage_section_is_rebuilt_from_defaults():
    migrated, _ = migrate_config({"matching": 5})
    assert migrated["matching"]["prefix_matching"] is False
    assert Config.from_dict({"matching": 5}).prefix_matching is False


def test_unknown_keys_survive_migration():
    migrated, _ = migrate_config({"something_we_dont_own": 42})
    assert migrated["something_we_dont_own"] == 42


def test_migration_does_not_mutate_input():
    original = {"prefix_matching": True, "matching": {"stem_matching": True}}
    migrate_config(original)
    assert original == {"prefix_matching": True, "matching": {"stem_matching": True}}
