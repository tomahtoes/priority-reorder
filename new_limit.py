"""today_new_limit: raise a deck's Today-only new card limit to fit the priority queue.

Anki ignores a Today-only limit once its `today` stops matching `sched.today`, so the
raise expires at the day rollover without the addon undoing anything, and the preset is
never touched. On the v3 scheduler the clicked deck's limit caps the whole session and a
subdeck's own limit caps what that subdeck contributes, which is why the configured deck
is the one the user clicks and its subdecks are checked too.
"""

from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

from anki.utils import ids2str

try:  # inside Anki: isolated package namespace
    from .reorder_log import NewLimitChange
except ImportError:  # pytest / flat-import context
    from reorder_log import NewLimitChange

# Collection config: {str(deck id): [day, limit]} for each Today-only limit the addon
# wrote today. A deck's current limit counts as the addon's only while it matches its
# entry exactly, so a limit the user typed in by hand, or edited afterwards, is left alone.
MARKER_KEY = "priorityReorderTodayLimits"

_UNCHANGED = object()


def apply_today_limits(
    col, config, priority_card_ids: Iterable[int]
) -> Tuple[List[NewLimitChange], list]:
    """Settle the Today-only limit of each configured deck and of its subdecks.

    Returns the per-deck report and the OpChanges of every deck write, for the caller
    to merge into the reorder's result so the deck browser refreshes its counts."""
    if not config.today_limit_enabled or not config.today_limit_decks:
        return [], []

    today = col.sched.today
    stored = col.get_config(MARKER_KEY, None)
    stored = stored if isinstance(stored, dict) else {}
    marker = {k: v for k, v in stored.items()
              if isinstance(v, list) and len(v) == 2 and v[0] == today}
    priority = set(priority_card_ids)

    named: Dict[str, int] = {}
    report: List[NewLimitChange] = []
    for name in config.today_limit_decks:
        did = col.decks.id_for_name(name)
        deck = col.decks.get(did, default=False) if did else None
        if not deck or deck.get("dyn"):
            report.append(NewLimitChange(deck=name, status="missing"))
        elif did not in named.values():
            named[name] = did

    named_ids = set(named.values())
    op_changes = []
    settled = set()
    for root in named.values():
        tree = col.decks.deck_and_child_ids(root)
        names = {d: col.decks.name(d) for d in tree}
        per_deck = Counter(
            did for cid, did in col.db.all(
                f"select id, did from cards where queue = 0 and did in {ids2str(tree)}")
            if cid in priority)
        for d in tree:
            if d in settled:
                continue
            prefix = names[d] + "::"
            count = sum(n for x, n in per_deck.items()
                        if x == d or names[x].startswith(prefix))
            # A subdeck only matters when it holds priority cards, or holds a limit
            # the addon wrote earlier today that may need clearing.
            if d not in named_ids and not count and str(d) not in marker:
                continue
            settled.add(d)
            deck = col.decks.get(d)
            change, new_limit = _settle(col, deck, count, today,
                                        marker.get(str(d)), config.today_limit_max)
            if new_limit is not _UNCHANGED:
                deck["newLimitToday"] = (None if new_limit is None
                                         else {"limit": new_limit, "today": today})
                op_changes.append(col.decks.update_dict(deck))
            if change.status == "raised":
                marker[str(d)] = [today, change.target]
            elif change.status in ("cleared", "hand_set"):
                marker.pop(str(d), None)
            if d in named_ids or change.status != "not_needed":
                report.append(change)

    # Unconditional writes would mark the collection modified on every reorder.
    if marker != stored:
        col.set_config(MARKER_KEY, marker)
    return report, op_changes


def _settle(col, deck: dict, count: int, today: int, owned: Optional[list],
            cap: Optional[int]) -> Tuple[NewLimitChange, object]:
    """Decide one deck's limit. Returns the report entry and the new Today-only limit
    to write: an int, None to clear it, or _UNCHANGED."""
    baseline = deck.get("newLimit")
    if baseline is None:
        baseline = col.decks.config_dict_for_deck_id(deck["id"])["new"]["perDay"]
    new_today = deck.get("newToday") or [0, 0]
    # Anki's limit counts cards already studied today, and studying a card bumps the
    # counter of its deck and every parent, so this covers the deck's whole subtree.
    studied = new_today[1] if new_today[0] == today else 0
    target = studied + count
    if cap is not None:
        target = min(target, cap)

    current = deck.get("newLimitToday")
    current_limit = current.get("limit") if current and current.get("today") == today else None
    ours = current_limit is not None and owned == [today, current_limit]

    def entry(status: str, value: int) -> NewLimitChange:
        return NewLimitChange(deck=deck["name"], status=status, baseline=baseline,
                              target=value, priority_count=count, studied=studied)

    if current_limit is not None and not ours:
        return entry("hand_set", current_limit), _UNCHANGED
    # Settled for the day once studying starts, so cards mined later don't keep
    # raising it. Before that, every reorder may still move it.
    if ours and studied:
        return entry("frozen", current_limit), _UNCHANGED
    if target > baseline:
        return entry("raised", target), (_UNCHANGED if current_limit == target else target)
    if ours:
        return entry("cleared", baseline), None
    return entry("not_needed", baseline), _UNCHANGED


def merge_op_changes(result, op_changes: list) -> None:
    """OR each OpChanges' flags into `result.changes` (an OpChangesWithCount)."""
    for changes in op_changes:
        for field, value in changes.ListFields():
            if value:
                setattr(result.changes, field.name, True)
