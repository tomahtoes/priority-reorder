from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

try:  # inside Anki: live collection available
    from aqt import mw
except ImportError:  # pytest / flat-import context (get_config is not exercised)
    mw = None

_VALID_SEARCH_MODES = ("sequential", "mix")

# Options are grouped into sections in config.json. `Config` itself stays flat, so
# everything that reads a setting keeps using `config.prefix_matching` etc.; only the
# JSON layout nests. `_GROUPS` maps each section to its keys and their defaults, and
# drives both parsing and the migration of older layouts.
#
# The names are chosen for how Anki's config editor renders them: it pretty-prints
# with sort_keys=True, so the only way to keep a section below the searches you tune
# day to day is a name that sorts after `sort_reverse`. `matching` is the deliberate
# exception, leading the file because it's the section worth seeing first.
_GROUPS = {
    "matching": {
        "kana_normalization": False,
        "combine_word_forms": False,
        "variant_matching": False,
        "stem_matching": False,
        "prefix_matching": False,
        "suffix_matching": False,
        "honorific_folding": False,
    },
    "sync_behavior": {
        "reorder_on_sync": True,
        "auto_update_dicts": False,
    },
    "tuning": {
        "priority_cutoff": None,
        "normal_prioritization": None,
        "priority_limit": None,
        "shift_existing": True,
    },
    "word_fields": {
        "expression_field": "Expression",
        "expression_reading_field": "ExpressionReading",
    },
}

# Retired section name -> current one.
_LEGACY_SECTIONS = {
    "queue_rules": "tuning",
    "automation": "sync_behavior",
    "search_fields": "word_fields",
}

# Flat key as it appeared in pre-section configs -> (section, canonical key). Only
# keys that were ever top-level belong here: `word_fields` has always been a section,
# so its keys are deliberately absent and a stray top-level `expression_field` is
# left alone rather than hoisted. The two retired sync spellings map onto
# reorder_on_sync, in the precedence order from_dict used to apply by hand.
_LEGACY_FLAT_KEYS = {
    key: (group, key)
    for group, keys in _GROUPS.items() if group != "word_fields"
    for key in keys
}
_LEGACY_FLAT_KEYS["reorder_after_sync"] = ("sync_behavior", "reorder_on_sync")
_LEGACY_FLAT_KEYS["reorder_before_sync"] = ("sync_behavior", "reorder_on_sync")

# Legacy keys an out-of-date installed config.json is contributing, which the user
# has not saved themselves. Populated by warn_if_defaults_stale(); empty on a healthy
# install, which is why the read path below normally does no extra work at all.
_stale_default_keys = frozenset()

# Most-preferred first: an explicit reorder_on_sync beats the older spellings.
_SYNC_ALIASES = ("reorder_on_sync", "reorder_after_sync", "reorder_before_sync")

def _warn(field_name: str, value: Any, reason: str) -> None:
    print(f"[priority-reorder] config: ignoring {field_name}={value!r} ({reason})")

def _coerce_bool(data: dict, key: str, default: bool) -> bool:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool):
        return value
    _warn(key, value, "expected bool")
    return default

def _coerce_str(data: dict, key: str, default: str) -> str:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, str):
        return value
    _warn(key, value, "expected string")
    return default

def _coerce_optional_int(data: dict, key: str) -> Optional[int]:
    if key not in data:
        return None
    value = data[key]
    if value is None:
        return None
    if isinstance(value, bool):
        _warn(key, value, "expected int or null")
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    _warn(key, value, "expected int or null")
    return None

def migrate_config(data: dict) -> tuple:
    """Normalize a stored config onto the current sectioned layout, returning
    `(migrated, changed)`.

    Pure, with no Anki imports, so it runs on every parse in `from_dict` as well as on the
    write-back path. Reading an older config must work identically whether or not the stored
    file was ever rewritten, which makes the write only a convenience.

    Three passes: fold retired section names into the current one, hoist pre-section flat keys
    into their section, and backfill missing section keys with defaults. The backfill matters
    because `getConfig` shallow-merges config.json over the user's dict, so a section the user
    has saved replaces the shipped default wholesale and later-added options would never reach
    their editor. Keys we don't own are left untouched.

    Legacy names win wherever both appear. The shipped config.json contains no legacy name (a
    test enforces this), so one in the input can only be the user's own saved value, while the
    current-named section beside it may be nothing but merged-in defaults.
    """
    migrated = dict(data)
    changed = False

    for group, defaults in _GROUPS.items():
        original = migrated.get(group)
        if isinstance(original, dict):
            section = dict(original)
        else:
            if original is not None:
                _warn(group, original, "expected object")
            section = {}

        # Retired section names first, so their entries are in place before the
        # backfill below can paper over them with defaults.
        for old_name, new_name in _LEGACY_SECTIONS.items():
            if new_name != group:
                continue
            old_section = migrated.get(old_name)
            if isinstance(old_section, dict):
                # Everything, not just keys we recognize: the retired section is
                # about to be deleted, so anything left behind is lost for good.
                section.update(old_section)
            elif old_section is not None:
                _warn(old_name, old_section, "expected object")

        for key, default in defaults.items():
            aliases = _SYNC_ALIASES if key == "reorder_on_sync" else (key,)
            flat = next((a for a in aliases if a in migrated
                         and _LEGACY_FLAT_KEYS.get(a) == (group, key)), None)
            if flat is not None:
                section[key] = migrated[flat]
            elif key not in section:
                section[key] = default

        if section != original:
            changed = True
        migrated[group] = section

    for legacy in list(_LEGACY_FLAT_KEYS) + list(_LEGACY_SECTIONS):
        if legacy in migrated:
            del migrated[legacy]
            changed = True

    return migrated, changed

@dataclass
class SearchConfig:
    expression_field: str = "Expression"
    expression_reading_field: str = "ExpressionReading"

@dataclass
class Config:
    priority_search: Union[str, List[str]] = ""
    priority_search_mode: str = "sequential"
    normal_search: str = ""
    sort_field: str = "FreqSort"
    sort_reverse: bool = False
    priority_cutoff: Optional[int] = None
    normal_prioritization: Optional[int] = None
    priority_limit: Optional[int] = None
    shift_existing: bool = True
    reorder_on_sync: bool = True
    auto_update_dicts: bool = False
    kana_normalization: bool = False
    combine_word_forms: bool = False
    variant_matching: bool = False
    stem_matching: bool = False
    prefix_matching: bool = False
    suffix_matching: bool = False
    honorific_folding: bool = False
    search_config: SearchConfig = field(default_factory=SearchConfig)

    @classmethod
    def from_dict(cls, data: dict) -> 'Config':
        # Migrating first means a pre-section config parses exactly like a migrated
        # one, whether or not the stored file was ever rewritten.
        data, _ = migrate_config(data)
        matching = data["matching"]
        sync_behavior = data["sync_behavior"]
        tuning = data["tuning"]
        word_fields = data["word_fields"]

        search_config = SearchConfig(
            expression_field=_coerce_str(word_fields, "expression_field", "Expression") or "Expression",
            expression_reading_field=_coerce_str(word_fields, "expression_reading_field", "ExpressionReading") or "ExpressionReading"
        )

        priority_search = data.get("priority_search", "")
        if not isinstance(priority_search, (str, list)):
            _warn("priority_search", priority_search, "expected string or list")
            priority_search = ""
        if isinstance(priority_search, list):
            priority_search = [s for s in priority_search if isinstance(s, str)]

        mode = _coerce_str(data, "priority_search_mode", "sequential")
        if mode not in _VALID_SEARCH_MODES:
            _warn("priority_search_mode", mode, f"expected one of {_VALID_SEARCH_MODES}")
            mode = "sequential"

        sort_field = _coerce_str(data, "sort_field", "FreqSort") or "FreqSort"

        return cls(
            priority_search=priority_search,
            priority_search_mode=mode,
            normal_search=_coerce_str(data, "normal_search", ""),
            sort_field=sort_field,
            sort_reverse=_coerce_bool(data, "sort_reverse", False),
            priority_cutoff=_coerce_optional_int(tuning, "priority_cutoff"),
            normal_prioritization=_coerce_optional_int(tuning, "normal_prioritization"),
            priority_limit=_coerce_optional_int(tuning, "priority_limit"),
            shift_existing=_coerce_bool(tuning, "shift_existing", True),
            reorder_on_sync=_coerce_bool(sync_behavior, "reorder_on_sync", True),
            auto_update_dicts=_coerce_bool(sync_behavior, "auto_update_dicts", False),
            kana_normalization=_coerce_bool(matching, "kana_normalization", False),
            combine_word_forms=_coerce_bool(matching, "combine_word_forms", False),
            variant_matching=_coerce_bool(matching, "variant_matching", False),
            stem_matching=_coerce_bool(matching, "stem_matching", False),
            prefix_matching=_coerce_bool(matching, "prefix_matching", False),
            suffix_matching=_coerce_bool(matching, "suffix_matching", False),
            honorific_folding=_coerce_bool(matching, "honorific_folding", False),
            search_config=search_config
        )

def get_config() -> Config:
    """Load configuration from Anki's config manager."""
    config_data = mw.addonManager.getConfig(__name__.split('.')[0]) or {}
    if _stale_default_keys:
        # An out-of-date config.json is contributing legacy keys the user never
        # saved. Migration would read them as intent and hoist them over the real
        # sections, silently reverting settings for the session. Drop them: they
        # carry no information, and the shipped defaults are already the fallback.
        config_data = {k: v for k, v in config_data.items()
                       if k not in _stale_default_keys}
    return Config.from_dict(config_data)

def _stored_config(pkg: str) -> dict:
    """The user's own saved config, without config.json's defaults merged in.

    `getConfig` returns defaults updated with the saved config, which is the right
    thing to *read* but the wrong thing to *migrate*: legacy names win during
    migration precisely because they can only come from the user, and a merged dict
    blurs that line the moment the installed config.json is out of date.
    """
    try:
        return mw.addonManager.addonMeta(pkg).get("config") or {}
    except Exception:  # older or changed Anki API; merged beats nothing
        return mw.addonManager.getConfig(pkg) or {}

def warn_if_defaults_stale(pkg: str) -> bool:
    """Warn when the installed config.json predates the current section layout.

    Anki merges those defaults into every read, so a stale file reintroduces legacy names that
    migration treats as user intent, quietly turning real settings back off for the session.
    Only happens with a half-copied install, which is otherwise invisible.

    Records the offending keys in `_stale_default_keys` so reads can ignore them. Only keys the
    user has *not* saved are recorded, since those can only have come from the stale defaults,
    whereas one the user really did save must still win.
    """
    global _stale_default_keys
    try:
        defaults = mw.addonManager.addonConfigDefaults(pkg) or {}
        legacy = set(_LEGACY_FLAT_KEYS) | set(_LEGACY_SECTIONS)
        stale = set(defaults) & legacy
        if not stale:
            _stale_default_keys = frozenset()
            return False
        _stale_default_keys = frozenset(stale - set(_stored_config(pkg)))
        print(f"[priority-reorder] config: the installed config.json is out of date "
              f"(it still defines {', '.join(sorted(stale))}). Update the addon's "
              f"files together; settings will not save until you do.")
        return True
    except Exception:
        return False

def migrate_config_in_place(pkg: str) -> dict:
    """Rewrite the user's stored config onto the current sectioned layout.

    Idempotent, and only writes when something actually moved. Returns the config as
    Anki would then serve it, so callers that also display it (the summary window's
    config button) don't have to re-read. Reading never depends on this having run.
    it exists so users' own config files quietly catch up instead of staying on a
    layout the docs no longer describe.
    """
    try:
        if warn_if_defaults_stale(pkg):
            # Refuse to rewrite against defaults we know are out of date. Reads
            # still work; a half-updated install is exactly the case where a write
            # could bake merged-in legacy defaults into the user's real config.
            return mw.addonManager.getConfig(pkg) or {}
        stored = _stored_config(pkg)
        if not stored:
            # Nothing saved yet: config.json is already the whole config, and
            # writing the backfill here would freeze today's defaults into a fresh
            # install that would otherwise pick up future changes to them.
            return mw.addonManager.getConfig(pkg) or {}
        migrated, changed = migrate_config(stored)
        if changed:
            mw.addonManager.writeConfig(pkg, migrated)
        return mw.addonManager.getConfig(pkg) or migrated
    except Exception as e:
        # A config we can't rewrite still reads fine; never block addon load. Fall
        # back to the stored dict rather than {}, since a caller may hand this to the
        # config editor, where an empty dict could be saved over a real config.
        print(f"[priority-reorder] config: could not migrate stored config ({e})")
        try:
            return mw.addonManager.getConfig(pkg) or {}
        except Exception:
            return {}
