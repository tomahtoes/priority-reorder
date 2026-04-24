from dataclasses import dataclass, field
from typing import Any, List, Optional, Union
from aqt import mw

_VALID_SEARCH_MODES = ("sequential", "mix")

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
    prefix_matching: bool = False
    honorific_folding: bool = False
    search_config: SearchConfig = field(default_factory=SearchConfig)

    @classmethod
    def from_dict(cls, data: dict) -> 'Config':
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
            priority_cutoff=_coerce_optional_int(data, "priority_cutoff"),
            normal_prioritization=_coerce_optional_int(data, "normal_prioritization"),
            priority_limit=_coerce_optional_int(data, "priority_limit"),
            shift_existing=_coerce_bool(data, "shift_existing", True),
            reorder_on_sync=_coerce_bool(data, "reorder_on_sync",
                _coerce_bool(data, "reorder_after_sync",
                    _coerce_bool(data, "reorder_before_sync", True))),
            auto_update_dicts=_coerce_bool(data, "auto_update_dicts", False),
            kana_normalization=_coerce_bool(data, "kana_normalization", False),
            combine_word_forms=_coerce_bool(data, "combine_word_forms", False),
            prefix_matching=_coerce_bool(data, "prefix_matching", False),
            honorific_folding=_coerce_bool(data, "honorific_folding", False),
            search_config=search_config
        )

def get_config() -> Config:
    """Load configuration from Anki's config manager."""
    config_data = mw.addonManager.getConfig(__name__.split('.')[0]) or {}
    return Config.from_dict(config_data)
