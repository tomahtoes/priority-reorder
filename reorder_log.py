from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class PrioritySearchStats:
    index: int
    query: str
    anki_query: str
    has_custom_rules: bool
    limit: Optional[int]
    raw_match_count: int = 0
    refined_match_count: int = 0
    cutoff_dropped: int = 0
    kept_count: int = 0
    limit_discarded: int = 0
    global_limit_discarded: int = 0
    kept_note_ids: List[int] = field(default_factory=list)
    discarded_note_ids: List[int] = field(default_factory=list)
    cutoff_note_ids: List[int] = field(default_factory=list)
    final_start_index: Optional[int] = None


@dataclass
class ReorderReport:
    timestamp: str
    mode: str
    priority_cutoff: Optional[int]
    global_priority_limit: Optional[int]
    entries: List[PrioritySearchStats] = field(default_factory=list)
    total_priority_kept: int = 0
    total_normal: int = 0
    total_repositioned: int = 0


def now_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_last_report: Optional[ReorderReport] = None


def set_last_report(report: ReorderReport) -> None:
    global _last_report
    _last_report = report


def get_last_report() -> Optional[ReorderReport]:
    return _last_report
