"""Tests for the summary window's page markup (summary_html), built headless."""

from reorder_log import PrioritySearchSummary, QueueSegment, ReorderReport
from summary_html import render_summary, shared_prefix


def entry(i, query, *, kept=0, matched=0, overlap=0, limit=None, limit_discarded=0,
          start=None):
    return PrioritySearchSummary(
        index=i, query=query, anki_query=query, has_custom_rules=False, limit=limit,
        refined_match_count=matched, raw_match_count=matched, kept_count=kept,
        overlap_count=overlap, limit_discarded=limit_discarded,
        kept_note_ids=list(range(kept)), final_start_index=start,
    )


def report(entries, **kw):
    base = dict(timestamp="2026-09-25 14:02:37", mode="sequential", priority_cutoff=None,
                global_priority_limit=None, entries=entries,
                total_priority_kept=sum(e.kept_count for e in entries), total_normal=100,
                total_repositioned=1)
    base.update(kw)
    return ReorderReport(**base)


# shared_prefix

def test_prefix_is_the_common_leading_terms():
    assert shared_prefix(["deck:A seen:7 x", "deck:A seen:7 y"]) == "deck:A seen:7"


def test_prefix_compares_whole_terms():
    assert shared_prefix(["deck:Foo x", "deck:Foobar y"]) == ""


def test_prefix_leaves_every_query_a_term_of_its_own():
    assert shared_prefix(["deck:A", "deck:A x"]) == ""
    assert shared_prefix(["deck:A x", "deck:A x y"]) == "deck:A"


def test_prefix_stops_at_a_group():
    assert shared_prefix(["deck:A (seen:7 OR added:7) x", "deck:A (seen:7 OR added:7) y"]) == "deck:A"


def test_top_level_or_disables_the_prefix():
    assert shared_prefix(["deck:A x OR y", "deck:A z"]) == ""


def test_no_prefix_for_a_single_search():
    assert shared_prefix(["deck:A x"]) == ""


# render_summary

def test_no_report_shows_the_empty_state():
    page = render_summary(None)
    assert "No reorder has run yet" in page
    assert "runReorder()" in page


def test_empty_rows_are_not_expandable():
    r = report([entry(0, "deck:A x", kept=2, matched=2, start=0),
                entry(1, "deck:A y", kept=0, matched=3, overlap=3)])
    page = render_summary(r)
    assert 'id="detail-0"' in page
    assert 'id="detail-1"' not in page
    assert '<tr class="row empty" id="row-1">' in page


def test_prefix_moves_to_the_header_and_out_of_the_rows():
    r = report([entry(0, "deck:A x", kept=1, matched=1, start=0),
                entry(1, "deck:A y", kept=1, matched=1, start=1)])
    page = render_summary(r)
    assert 'class="thprefix">· deck:A</span>' in page
    assert "deck:A x" not in page.split("<tbody>")[1].split("title=")[0]


def test_legend_hides_parts_for_settings_that_are_off():
    r = report([entry(0, "q0", kept=1, matched=1, start=0)])
    page = render_summary(r)
    assert "taken by earlier search" not in page
    assert "over limit" not in page
    assert "below cutoff" not in page
    assert "promoted tier" not in page

    r = report([entry(0, "q0", kept=1, matched=3, overlap=1, limit=1, limit_discarded=1,
                      start=0)], priority_cutoff=5000, promoted_count=4)
    page = render_summary(r)
    for label in ("taken by earlier search", "over limit", "below cutoff", "promoted tier"):
        assert label in page, label


def test_kept_delta_against_the_previous_run_by_query():
    prev = report([entry(0, "q0", kept=5, matched=5, start=0)])
    cur = report([entry(0, "new search", kept=1, matched=1, start=0),
                  entry(1, "q0", kept=3, matched=3, start=1)])
    page = render_summary(cur, prev)
    assert 'class="delta down"' in page and "−2" in page
    assert page.count('class="delta') == 1   # the new search has no history


def test_queue_positions_are_one_based_ranges():
    r = report([entry(0, "q0", kept=3, matched=3, start=0),
                entry(1, "q1", kept=1, matched=1, start=3)])
    page = render_summary(r)
    assert '<td class="pos">1–3</td>' in page
    assert '<td class="pos">4</td>' in page


def test_cycle_mode_shows_spread_for_searches_that_took_several_turns():
    spread = entry(0, "q0 limit=2", kept=5, matched=5, limit=2, start=0)
    spread.turns, spread.first_turn_count, spread.later_start, spread.last_index = 3, 2, 5, 8
    single = entry(1, "q1", kept=3, matched=3, start=2)
    single.turns, single.first_turn_count, single.last_index = 1, 3, 4
    page = render_summary(report([spread, single], mode="cycle"))
    assert "Cycle mode: searches take turns" in page
    assert "<th>Queue</th>" in page
    # The gap between the first turn and the later ones is not claimed.
    assert ('<td class="pos" title="First turn 1–2, then 2 more turns, last card at 9">'
            "1–2, 6–9 ↻</td>") in page
    assert '<td class="pos">3–5</td>' in page
    assert "over limit" not in page


def _cycled(start, first_turn, later_start, last, kept):
    e = entry(0, "q0 limit=2", kept=kept, matched=kept, limit=2, start=start)
    e.turns, e.first_turn_count, e.later_start, e.last_index = 2, first_turn, later_start, last
    return render_summary(report([e], mode="cycle"))


def test_cycle_range_merges_when_later_turns_follow_the_first_directly():
    # Later turns start right after the first turn but share the stretch with others.
    assert "1–9 ↻</td>" in _cycled(start=0, first_turn=2, later_start=2, last=8, kept=5)


def test_cycle_range_is_plain_when_the_search_ended_up_in_one_block():
    # The only search still taking turns: its cards are contiguous after all.
    assert '<td class="pos" title="First turn 1–2, then 1 more turn, last card at 5">1–5</td>' in (
        _cycled(start=0, first_turn=2, later_start=2, last=4, kept=5))


def test_cycle_segment_draws_a_striped_stretch_after_the_first_pass():
    r = report([entry(0, "q0 limit=2", kept=5, matched=5, limit=2),
                entry(1, "q1 limit=1", kept=2, matched=2, limit=1)], mode="cycle",
               queue_segments=[
                   QueueSegment(kind="search", start=0, count=2, search=0),
                   QueueSegment(kind="search", start=2, count=1, search=1),
                   QueueSegment(kind="cycle", start=3, count=4, cycled={0: 3, 1: 1}),
               ])
    page = render_summary(r)
    assert page.count('class="seg"') == 2
    assert 'class="seg cyc" style="flex:4" data-row="0"' in page
    assert "Searches taking turns, positions 4–7" in page
    assert "[1] 3 cards" in page and "[2] 1 cards" in page
    assert ">1·2</div>" in page
    assert "taking turns</span>" in page


def test_cycle_mode_without_limits_renders_like_sequential():
    entries = lambda: [entry(0, "q0", kept=3, matched=3, start=0),
                       entry(1, "q1", kept=1, matched=1, start=3)]
    assert render_summary(report(entries(), mode="cycle")) == render_summary(report(entries()))


def test_skipped_reorder_says_so():
    r = report([entry(0, "q0", kept=1, matched=1, start=0)], total_repositioned=0)
    assert "already in order, nothing moved" in render_summary(r)


def test_open_rows_render_expanded():
    r = report([entry(0, "q0", kept=1, matched=1, start=0)])
    assert '<tr class="detail" hidden id="detail-0"' in render_summary(r)
    assert '<tr class="detail" id="detail-0"' in render_summary(r, open_rows={0})


def test_mix_mode_drops_the_per_search_queue_columns():
    r = report([entry(0, "q0", matched=4), entry(1, "q1", matched=0)], mode="mix")
    page = render_summary(r)
    assert "<th>Queue</th>" not in page
    assert 'class="qbar"' not in page
    assert '<tr class="row empty" id="row-1">' in page
