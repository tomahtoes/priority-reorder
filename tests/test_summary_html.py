"""Tests for the summary window's page markup (summary_html), built headless."""

from reorder_log import PrioritySearchSummary, ReorderReport
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
