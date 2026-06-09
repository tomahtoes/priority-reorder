"""Unit tests for the core reordering pipeline (reorderer.PriorityReorderer).

These drive the *pure* bucket/sort/limit methods directly with hand-built Card
objects — no live collection needed — and pin the behavior of the bugs this
project has already hit once:

  * 19f8f16  limit= must fall through to *later* sequential buckets; empty kept
             buckets must still be appended (index↔def alignment).
  * 992df57  low-value normals are promoted into their *own* trailing tier (not
             the last search bucket); cards with no sort value always trail.
"""

from anki.collection import OpChangesWithCount

from config_manager import Config
from models import Card, NoteData
from reorder_log import PrioritySearchStats
from reorderer import PriorityReorderer


# --- builders ---------------------------------------------------------------

def card(cid, sort=None, nid=None):
    """A Card with card_id=cid (note_id defaults to cid). `sort=None` means the
    note has no usable sort value (sentinel +inf, has_sort_value=False)."""
    nid = cid if nid is None else nid
    if sort is None:
        data = NoteData(note_id=nid, sort_field_value=float("inf"), has_sort_value=False)
    else:
        data = NoteData(note_id=nid, sort_field_value=float(sort), has_sort_value=True)
    return Card(card_id=cid, note_id=nid, data=data)


def reorderer(**cfg):
    return PriorityReorderer(Config(**cfg))


def stats(n):
    return [
        PrioritySearchStats(index=i, query=f"q{i}", anki_query=f"q{i}",
                            has_custom_rules=False, limit=None)
        for i in range(n)
    ]


def ids(cards):
    return [c.card_id for c in cards]


# --- _sort_cards ------------------------------------------------------------

def test_sort_ascending_missing_values_trail():
    r = reorderer(sort_reverse=False)
    out = r._sort_cards([card(1, 5), card(2, 1), card(3, None), card(4, 3)])
    assert ids(out) == [2, 4, 1, 3]  # 1,3,5 ascending then the value-less card


def test_sort_reverse_missing_values_still_trail():
    # Regression (992df57): a numeric sentinel would float the value-less card to
    # the top under reverse=True; it must always trail instead.
    r = reorderer(sort_reverse=True)
    out = r._sort_cards([card(1, 5), card(2, 1), card(3, None), card(4, 3)])
    assert ids(out) == [1, 4, 2, 3]  # 5,3,1 descending then the value-less card


# --- _assign_initial_buckets ------------------------------------------------

def _card_map(*cards):
    return {c.card_id: c for c in cards}


def test_assign_buckets_sequential_one_bucket_per_def():
    r = reorderer(priority_search_mode="sequential")
    cm = _card_map(card(1), card(2), card(3), card(4), card(5))
    defs = [("q0", None), ("q1", None)]
    matches = {0: {1, 2}, 1: {2, 3}}
    all_ids = {1, 2, 3, 4, 5}

    buckets, normal = r._assign_initial_buckets(defs, matches, all_ids, cm)

    assert len(buckets) == 2
    assert {c.card_id for c in buckets[0]} == {1, 2}
    assert {c.card_id for c in buckets[1]} == {2, 3}  # overlap kept; dedup is later
    assert {c.card_id for c in normal} == {4, 5}      # all_ids minus any match


def test_assign_buckets_mix_unions_into_single_bucket():
    r = reorderer(priority_search_mode="mix")
    cm = _card_map(card(1), card(2), card(3), card(4), card(5))
    defs = [("q0", None), ("q1", None)]
    matches = {0: {1, 2}, 1: {2, 3}}
    all_ids = {1, 2, 3, 4, 5}

    buckets, normal = r._assign_initial_buckets(defs, matches, all_ids, cm)

    assert len(buckets) == 1
    assert {c.card_id for c in buckets[0]} == {1, 2, 3}
    assert {c.card_id for c in normal} == {4, 5}


# --- _apply_refinement_rules ------------------------------------------------

def test_cutoff_moves_over_threshold_cards_to_normal_with_stats():
    r = reorderer(priority_search_mode="sequential", priority_cutoff=10)
    st = stats(1)
    over = card(2, 20)
    buckets = [[card(1, 5), over]]

    final_priority, normal = r._apply_refinement_rules(buckets, [card(9, 30)], st)

    assert ids(final_priority[0]) == [1]            # only the under-cutoff card kept
    assert over.card_id in ids(normal)              # dropped to normal
    assert st[0].cutoff_dropped == 1
    assert st[0].cutoff_note_ids == [over.note_id]


def test_cutoff_in_mix_mode_does_not_touch_per_search_stats():
    r = reorderer(priority_search_mode="mix", priority_cutoff=10)
    st = stats(1)
    buckets = [[card(1, 5), card(2, 20)]]

    final_priority, normal = r._apply_refinement_rules(buckets, [], st)

    assert 2 in ids(normal)          # still dropped to normal
    assert st[0].cutoff_dropped == 0  # but mix mode records no per-search stat


def test_prioritization_promotes_into_separate_trailing_tier():
    # Regression (992df57): promoted normals form their OWN tier, not the last
    # search bucket (otherwise they'd be subject to that search's limit/stats).
    r = reorderer(priority_search_mode="sequential", normal_prioritization=100)
    st = stats(1)
    buckets = [[card(1, 5)]]
    normal = [card(50, 50), card(200, 200)]  # 50 <= 100 promoted; 200 stays

    final_priority, new_normal = r._apply_refinement_rules(buckets, normal, st)

    assert len(final_priority) == 2          # original bucket + dedicated tier
    assert ids(final_priority[0]) == [1]     # search bucket untouched
    assert ids(final_priority[-1]) == [50]   # promoted card in its own tier
    assert ids(new_normal) == [200]


def test_empty_kept_bucket_is_still_appended_for_index_alignment():
    # Regression (19f8f16): dropping empty buckets would misalign bucket↔def↔stats
    # indices for later searches.
    r = reorderer(priority_search_mode="sequential", priority_cutoff=10)
    st = stats(2)
    buckets = [[card(1, 20)], [card(2, 5)]]  # bucket 0 entirely over the cutoff

    final_priority, _ = r._apply_refinement_rules(buckets, [], st)

    assert final_priority[0] == []
    assert ids(final_priority[1]) == [2]


# --- _finalize_priority_queue -----------------------------------------------

def test_limit_overflow_falls_through_to_later_bucket():
    # Regression (19f8f16): a card over bucket 0's limit must still be placed if a
    # later bucket also matches it, instead of being silently dropped.
    r = reorderer(priority_search_mode="sequential")
    st = stats(2)
    cA, cB, cC = card(10, 1), card(20, 2), card(30, 3)
    defs = [("q0", 1), ("q1", None)]       # bucket 0 keeps only its top card
    buckets = [[cA, cB], [cB, cC]]          # cB matches both searches

    queue, overflow = r._finalize_priority_queue(defs, buckets, st)

    assert ids(queue) == [10, 20, 30]       # cB fell through to bucket 1
    assert cB.card_id in ids(queue)
    assert st[0].kept_count == 1            # bucket 0 kept only cA
    assert st[0].limit_discarded == 1       # cB counted as over-limit in bucket 0
    assert st[1].kept_count == 2            # cB + cC placed via bucket 1


def test_sequential_dedup_card_counts_only_in_earliest_bucket():
    r = reorderer(priority_search_mode="sequential")
    st = stats(2)
    shared = card(5, 1)
    defs = [("q0", None), ("q1", None)]
    buckets = [[shared], [shared, card(6, 2)]]

    queue, _ = r._finalize_priority_queue(defs, buckets, st)

    assert ids(queue) == [5, 6]             # shared placed once, by bucket 0
    assert st[0].kept_note_ids == [shared.note_id]
    assert st[1].kept_note_ids == [6]       # bucket 1 only credits the new card


def test_global_priority_limit_clips_queue_into_overflow():
    r = reorderer(priority_search_mode="sequential", priority_limit=2)
    st = stats(1)
    defs = [("q0", None)]
    buckets = [[card(1, 1), card(2, 2), card(3, 3)]]

    queue, overflow = r._finalize_priority_queue(defs, buckets, st)

    assert ids(queue) == [1, 2]
    assert ids(overflow) == [3]
    assert st[0].kept_count == 2
    assert st[0].global_limit_discarded == 1


def test_final_start_index_is_cumulative_kept_of_prior_searches():
    r = reorderer(priority_search_mode="sequential")
    st = stats(2)
    defs = [("q0", None), ("q1", None)]
    buckets = [[card(1, 1), card(2, 2)], [card(3, 3), card(4, 4)]]

    r._finalize_priority_queue(defs, buckets, st)

    assert st[0].final_start_index == 0
    assert st[1].final_start_index == 2     # follows the 2 kept by search 0


def test_final_start_index_none_for_search_with_zero_kept():
    r = reorderer(priority_search_mode="sequential")
    st = stats(2)
    defs = [("q0", None), ("q1", None)]
    buckets = [[card(1, 1)], []]             # search 1 keeps nothing

    r._finalize_priority_queue(defs, buckets, st)

    assert st[0].final_start_index == 0
    assert st[1].final_start_index is None


def test_mix_mode_flattens_and_sorts_all_buckets_together():
    r = reorderer(priority_search_mode="mix")
    st = stats(1)
    defs = [("q0", None)]
    buckets = [[card(1, 3), card(2, 1), card(3, None)]]

    queue, overflow = r._finalize_priority_queue(defs, buckets, st)

    assert ids(queue) == [2, 1, 3]          # sorted asc, value-less card last
    assert overflow == []


# --- _apply_reordering (thin integration over a fake scheduler) -------------

class _FakeSched:
    def __init__(self):
        self.calls = []

    def reposition_new_cards(self, card_ids, **kwargs):
        self.calls.append((list(card_ids), kwargs))
        return OpChangesWithCount(count=len(list(card_ids)))


def test_apply_reordering_dedups_and_repositions_in_order(monkeypatch):
    import reorderer as rmod
    import types

    sched = _FakeSched()
    monkeypatch.setattr(rmod.mw, "col", types.SimpleNamespace(sched=sched), raising=False)

    r = reorderer(sort_reverse=False, shift_existing=True)
    dup = card(2, 2)
    priority = [card(1, 1), dup]
    normal = [dup, card(3, 3)]   # dup also in normal -> must appear once

    result = r._apply_reordering(priority, normal)

    assert sched.calls[0][0] == [1, 2, 3]            # priority first, deduped
    assert sched.calls[0][1]["shift_existing"] is True
    assert result.count == 3


def test_apply_reordering_no_cards_does_not_call_scheduler(monkeypatch):
    import reorderer as rmod
    import types

    sched = _FakeSched()
    monkeypatch.setattr(rmod.mw, "col", types.SimpleNamespace(sched=sched), raising=False)

    result = reorderer()._apply_reordering([], [])

    assert sched.calls == []
    assert result.count == 0
