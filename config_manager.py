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
# drives both parsing and the migration of older flat configs.
_GROUPS = {
    "queue_rules": {
        "priority_cutoff": None,
        "normal_prioritization": None,
        "priority_limit": None,
        "shift_existing": True,
    },
    "matching": {
        "kana_normalization": False,
        "combine_word_forms": False,
        "variant_matching": False,
        "stem_matching": False,
        "prefix_matching": False,
        "suffix_matching": False,
        "honorific_folding": False,
    },
    "automation": {
        "reorder_on_sync": True,
        "auto_update_dicts": False,
    },
}

# Flat key as it appeared in pre-section configs -> (section, canonical key). Every
# grouped key is its own legacy name; the two retired sync spellings map onto
# reorder_on_sync, in the same precedence order from_dict used to apply by hand.
_LEGACY_KEYS = {
    key: (group, key) for group, keys in _GROUPS.items() for key in keys
}
_LEGACY_KEYS["reorder_after_sync"] = ("automation", "reorder_on_sync")
_LEGACY_KEYS["reorder_before_sync"] = ("automation", "reorder_on_sync")

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
    """Normalize a stored config onto the current sectioned layout.

    Returns `(migrated, changed)`. Pure — no Anki imports — so it runs on every
    parse in `from_dict` as well as on the write-back path. That is deliberate:
    reading an unmigrated config must work identically whether or not the stored
    file was ever rewritten, so the write is only ever a convenience.

    Two things happen: user values living under a pre-section flat key are hoisted
    into their section (and the flat key dropped), then any section key still
    missing is backfilled with its default, so Anki's JSON editor always shows the
    complete current schema. The backfill matters because `getConfig` shallow-merges
    config.json over the user's dict: a section the user has saved replaces the
    shipped default wholesale, so options added in later versions would otherwise
    never reach an existing user's editor. Keys we don't own are left untouched.
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

        for key, default in defaults.items():
            aliases = _SYNC_ALIASES if key == "reorder_on_sync" else (key,)
            flat = next((a for a in aliases if a in migrated), None)
            if flat is not None:
                # A flat key present at all means this config predates the sections
                # (migration always removes them), so it carries the user's real
                # value while the section alongside it is just the shipped default
                # Anki merged in. It therefore wins outright.
                section[key] = migrated[flat]
            elif key not in section:
                section[key] = default

        if section != original:
            changed = True
        migrated[group] = section

    for legacy in _LEGACY_KEYS:
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
        queue = data["queue_rules"]
        matching = data["matching"]
        automation = data["automation"]

        search_config_data = data.get("search_fields", {}) or {}
        search_config = SearchConfig(
            expression_field=_coerce_str(search_config_data, "expression_field", "Expression") or "Expression",
            expression_reading_field=_coerce_str(search_config_data, "expression_reading_field", "ExpressionReading") or "ExpressionReading"
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
            priority_cutoff=_coerce_optional_int(queue, "priority_cutoff"),
            normal_prioritization=_coerce_optional_int(queue, "normal_prioritization"),
            priority_limit=_coerce_optional_int(queue, "priority_limit"),
            shift_existing=_coerce_bool(queue, "shift_existing", True),
            reorder_on_sync=_coerce_bool(automation, "reorder_on_sync", True),
            auto_update_dicts=_coerce_bool(automation, "auto_update_dicts", False),
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
    return Config.from_dict(config_data)

def migrate_config_in_place(pkg: str) -> dict:
    """Rewrite the user's stored config onto the current sectioned layout.

    Idempotent, and only writes when something actually moved. Returns the migrated
    dict so callers that also want to display it (the summary window's config
    button) don't have to re-read. Reading never depends on this having run — it
    exists so users' own config files quietly catch up instead of staying on a
    layout the docs no longer describe.
    """
    try:
        migrated, changed = migrate_config(mw.addonManager.getConfig(pkg) or {})
        if changed:
            mw.addonManager.writeConfig(pkg, migrated)
        return migrated
    except Exception as e:
        # A config we can't rewrite still reads fine; never block addon load. Fall
        # back to the stored dict rather than {} — a caller may hand this to the
        # config editor, where an empty dict could be saved over a real config.
        print(f"[priority-reorder] config: could not migrate stored config ({e})")
        try:
            return mw.addonManager.getConfig(pkg) or {}
        except Exception:
            return {}
