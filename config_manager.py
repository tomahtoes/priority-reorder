from dataclasses import dataclass, field
from typing import List, Optional, Union, Dict
from aqt import mw

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
    search_config: SearchConfig = field(default_factory=SearchConfig)

    @classmethod
    def from_dict(cls, data: dict) -> 'Config':
        search_config_data = data.get("search_fields", {})
        search_config = SearchConfig(
            expression_field=search_config_data.get("expression_field", "Expression"),
            expression_reading_field=search_config_data.get("expression_reading_field", "ExpressionReading")
        )
        
        return cls(
            priority_search=data.get("priority_search", ""),
            priority_search_mode=data.get("priority_search_mode", "sequential"),
            normal_search=data.get("normal_search", ""),
            sort_field=data.get("sort_field", "FreqSort"),
            sort_reverse=data.get("sort_reverse", False),
            priority_cutoff=data.get("priority_cutoff"),
            normal_prioritization=data.get("normal_prioritization"),
            priority_limit=data.get("priority_limit"),
            shift_existing=data.get("shift_existing", True),
            reorder_on_sync=data.get("reorder_on_sync", data.get("reorder_after_sync", data.get("reorder_before_sync", True))),
            search_config=search_config
        )

def get_config() -> Config:
    """Load configuration from Anki's config manager."""
    config_data = mw.addonManager.getConfig(__name__.split('.')[0]) or {}
    return Config.from_dict(config_data)
