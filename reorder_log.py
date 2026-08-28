import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class PrioritySearchSummary:
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
    entries: List[PrioritySearchSummary] = field(default_factory=list)
    total_priority_kept: int = 0
    total_normal: int = 0
    total_repositioned: int = 0
    # Per-stage wall-clock durations of the reorder run, in milliseconds.
    timings_ms: Dict[str, float] = field(default_factory=dict)


def now_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_last_report: Optional[ReorderReport] = None


def set_last_report(report: ReorderReport) -> None:
    global _last_report
    _last_report = report


def clear_last_report() -> None:
    """Drop the stored report (used on profile switch, since note ids from one profile
    must not be shown or opened in another)."""
    global _last_report
    _last_report = None


def get_last_report() -> Optional[ReorderReport]:
    return _last_report


_TIMINGS_LOG_MAX_LINES = 200


def _timings_log_path() -> str:
    # A plain file at the user_files root: invisible to dictionary enumeration
    # (which filters to directories) and preserved across addon updates.
    return os.path.join(os.path.dirname(__file__), "user_files", "_timings.log")


def append_timings_line(timestamp: str, timings_ms: Dict[str, float]) -> None:
    """Append one 'ts  k=vms ...' line to the timings log, trimming it to the
    last _TIMINGS_LOG_MAX_LINES. Opt-in via reorderer._DUMP_TIMINGS_LOG; must
    never raise, because timings logging can never break a reorder."""
    try:
        line = timestamp + "  " + " ".join(f"{k}={v}ms" for k, v in timings_ms.items())
        path = _timings_log_path()
        lines: List[str] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            pass  # missing/unreadable file -> start fresh
        lines.append(line)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines[-_TIMINGS_LOG_MAX_LINES:]) + "\n")
    except Exception:
        pass
