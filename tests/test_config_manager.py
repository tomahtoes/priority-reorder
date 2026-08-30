from config_manager import (Config, migrate_config,
                            _LEGACY_FLAT_KEYS, _LEGACY_SECTIONS)


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


# back-compat: pre-section flat configs
# The tests above all pass flat dicts and must keep passing untouched. That is the
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
    assert migrated["tuning"]["priority_limit"] == 200
    assert migrated["tuning"]["shift_existing"] is False
    assert migrated["sync_behavior"]["auto_update_dicts"] is True
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
        "tuning": {"priority_cutoff": 5000},
        "sync_behavior": {"reorder_on_sync": False},
    })
    assert c.variant_matching is True
    assert c.stem_matching is True
    assert c.priority_cutoff == 5000
    assert c.reorder_on_sync is False


def test_flat_key_wins_over_the_section_beside_it():
    # This is the shape getConfig actually hands us for an unmigrated user: Anki
    # shallow-merges config.json, so the default sections sit alongside the user's
    # real flat values. A flat key present at all means the config predates the
    # sections (migration always removes them), so it must win. Otherwise every
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


def test_legacy_sync_aliases_land_in_sync_behavior():
    assert migrate_config({"reorder_after_sync": False})[0]["sync_behavior"]["reorder_on_sync"] is False
    assert migrate_config({"reorder_before_sync": False})[0]["sync_behavior"]["reorder_on_sync"] is False
    # precedence is unchanged: the canonical key beats the older spellings
    migrated, _ = migrate_config({"reorder_on_sync": True, "reorder_after_sync": False,
                                  "reorder_before_sync": False})
    assert migrated["sync_behavior"]["reorder_on_sync"] is True
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
        "compound_matching", "prefix_matching", "suffix_matching", "honorific_folding",
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


# back-compat: sections renamed after the first sectioned release


def test_retired_section_names_migrate_with_values_intact():
    migrated, changed = migrate_config({
        "queue_rules": {"priority_limit": 200, "shift_existing": False},
        "automation": {"reorder_on_sync": False, "auto_update_dicts": True},
        "search_fields": {"expression_field": "Word", "expression_reading_field": "Kana"},
    })
    assert changed is True
    assert migrated["tuning"]["priority_limit"] == 200
    assert migrated["tuning"]["shift_existing"] is False
    assert migrated["sync_behavior"]["reorder_on_sync"] is False
    assert migrated["sync_behavior"]["auto_update_dicts"] is True
    assert migrated["word_fields"]["expression_field"] == "Word"
    assert not any(k in migrated for k in ("queue_rules", "automation", "search_fields"))


def test_search_fields_still_parses_into_search_config():
    # search_fields shipped in a real release; it must keep working untouched.
    c = Config.from_dict({"search_fields": {
        "expression_field": "Word",
        "expression_reading_field": "Kana",
    }})
    assert c.search_config.expression_field == "Word"
    assert c.search_config.expression_reading_field == "Kana"


def test_retired_section_wins_over_the_current_one_beside_it():
    # The shallow-merge shape again: config.json supplies `tuning` full of defaults
    # while the user's saved config still calls it `queue_rules`.
    migrated, _ = migrate_config({
        "queue_rules": {"priority_limit": 200},
        "tuning": {"priority_limit": None, "shift_existing": True},
    })
    assert migrated["tuning"]["priority_limit"] == 200


def test_flat_config_migrates_straight_to_current_names():
    # Two schema hops in one pass, with no intermediate queue_rules/automation state.
    migrated, _ = migrate_config({"prefix_matching": True, "priority_limit": 200,
                                  "reorder_before_sync": False})
    assert migrated["matching"]["prefix_matching"] is True
    assert migrated["tuning"]["priority_limit"] == 200
    assert migrated["sync_behavior"]["reorder_on_sync"] is False
    assert set(migrated) == {"matching", "sync_behavior", "tuning", "word_fields"}


def test_stray_top_level_field_key_is_not_hoisted():
    # word_fields was always a section, so its keys were never legacy flat keys.
    migrated, _ = migrate_config({"expression_field": "Word"})
    assert migrated["word_fields"]["expression_field"] == "Expression"
    assert migrated["expression_field"] == "Word"  # left alone, not ours to move


def test_shipped_defaults_contain_no_legacy_names():
    # The invariant the whole legacy-wins precedence rests on: if config.json ever
    # ships a legacy name again, migration would read merged-in defaults as user
    # intent and quietly reset real settings.
    import json, os
    defaults = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json"),
                              encoding="utf-8"))
    assert not set(defaults) & set(_LEGACY_FLAT_KEYS)
    assert not set(defaults) & set(_LEGACY_SECTIONS)


# data preservation


def test_unknown_keys_inside_a_section_survive():
    migrated, _ = migrate_config({"matching": {"prefix_matching": True, "mine": 1}})
    assert migrated["matching"]["mine"] == 1


def test_unknown_keys_survive_a_section_rename():
    # The retired section is deleted afterwards, so anything not carried over is
    # gone for good.
    migrated, _ = migrate_config({"queue_rules": {"priority_limit": 200, "mine": 1}})
    assert migrated["tuning"]["priority_limit"] == 200
    assert migrated["tuning"]["mine"] == 1


def test_migration_never_drops_a_user_value():
    # Property check over a config using every layout at once: every leaf value the
    # user stored must still be findable afterwards, wherever migration moved it.
    original = {
        "priority_search": ["deck:A", "deck:B"], "sort_field": "Freq",
        "prefix_matching": True, "priority_limit": 200, "reorder_before_sync": False,
        "queue_rules": {"normal_prioritization": 50, "custom": "keep"},
        "matching": {"stem_matching": True},
        "search_fields": {"expression_field": "Word"},
        "totally_unknown": {"nested": [1, 2]},
    }
    migrated, _ = migrate_config(original)

    def leaves(d):
        for k, v in d.items():
            if isinstance(v, dict):
                yield from leaves(v)
            else:
                yield (k, repr(v))

    before, after = set(leaves(original)), set(leaves(migrated))
    # reorder_before_sync is renamed to reorder_on_sync, so compare on value alone
    # for that one; everything else must survive key and value intact.
    missing = {(k, v) for k, v in before - after
               if not (k == "reorder_before_sync" and ("reorder_on_sync", v) in after)}
    assert not missing, f"migration dropped: {missing}"


# an out-of-date installed config.json
# Anki merges config.json's defaults into every read, so a half-updated install
# (new .py files, old config.json) reintroduces legacy keys. Those must not be
# mistaken for user intent. The failure is silent: settings simply read as off.

import copy

import config_manager


class _FakeAddonManager:
    def __init__(self, stored, defaults):
        self.stored, self.defaults, self.writes = stored, defaults, 0

    def addonConfigDefaults(self, pkg):
        return copy.deepcopy(self.defaults)

    def addonMeta(self, pkg):
        return {"config": copy.deepcopy(self.stored)}

    def getConfig(self, pkg):
        merged = copy.deepcopy(self.defaults)
        merged.update(self.stored)
        return merged

    def writeConfig(self, pkg, conf):
        self.writes += 1
        self.stored = conf


class _FakeMw:
    def __init__(self, stored, defaults):
        self.addonManager = _FakeAddonManager(stored, defaults)


STALE_DEFAULTS = {"prefix_matching": False, "priority_limit": None,
                  "shift_existing": True,
                  "search_fields": {"expression_field": "Expression"}}
SECTIONED_USER = {"matching": {"prefix_matching": True},
                  "tuning": {"priority_limit": 500, "shift_existing": False},
                  "word_fields": {"expression_field": "Word"}}


def _install(monkeypatch, stored, defaults):
    mw = _FakeMw(copy.deepcopy(stored), defaults)
    monkeypatch.setattr(config_manager, "mw", mw)
    monkeypatch.setattr(config_manager, "_stale_default_keys", frozenset())
    return mw


def test_stale_defaults_are_never_written_over_a_real_config(monkeypatch):
    mw = _install(monkeypatch, SECTIONED_USER, STALE_DEFAULTS)
    config_manager.migrate_config_in_place("pr")
    assert mw.addonManager.writes == 0
    assert mw.addonManager.stored == SECTIONED_USER


def test_stale_defaults_do_not_silently_revert_settings_on_read(monkeypatch):
    mw = _install(monkeypatch, SECTIONED_USER, STALE_DEFAULTS)
    config_manager.migrate_config_in_place("pr")   # what addon load does
    c = config_manager.get_config()
    assert c.prefix_matching is True
    assert c.priority_limit == 500
    assert c.shift_existing is False
    assert c.search_config.expression_field == "Word"


def test_a_legacy_key_the_user_really_saved_still_wins(monkeypatch):
    # Only keys absent from the user's own saved config are treated as stale noise.
    _install(monkeypatch, {"prefix_matching": True}, STALE_DEFAULTS)
    config_manager.migrate_config_in_place("pr")
    assert config_manager.get_config().prefix_matching is True


def test_healthy_install_does_no_stale_filtering(monkeypatch):
    import json, os
    defaults = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json"),
                              encoding="utf-8"))
    _install(monkeypatch, SECTIONED_USER, defaults)
    assert config_manager.warn_if_defaults_stale("pr") is False
    assert config_manager._stale_default_keys == frozenset()
    assert config_manager.get_config().prefix_matching is True
