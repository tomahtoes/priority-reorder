"""HTML for the summary window, rendered inside an AnkiWebView.

Pure module (no ``aqt`` import) so the page can be built and tested headless. The
window (summary_window.py) owns the webview and answers the ``pycmd`` messages the
page sends: ``close``, ``config``, ``reorder``, ``toggle:<i>`` and
``browse:<kept|discarded|cutoff>:<i>``.
"""

import html
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set

try:
    from .reorder_log import (NewLimitChange, PrioritySearchSummary, QueueSegment,
                              ReorderReport)
    from .search_colors import colorize_query_html
except ImportError:  # pytest / flat-import context
    from reorder_log import (NewLimitChange, PrioritySearchSummary, QueueSegment,
                             ReorderReport)
    from search_colors import colorize_query_html

# Same tokenizer as search_colors: quoted blocks stay one token.
_TOKEN_RE = re.compile(r'(?:"[^"]*"|[^\s"])+')

_TRIGGER_TEXT = {"sync": "after sync", "close": "on close"}

# A queue-bar segment gets its search number printed inside it only when it holds at
# least this share of the prioritized cards; narrower segments rely on the tooltip.
_SEGMENT_LABEL_SHARE = 0.04


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _fmt(n: int) -> str:
    return f"{n:,}"


def shared_prefix(queries: List[str]) -> str:
    """The leading search terms every query starts with, or "" if none can be shown once.

    Compared on whole terms, so `deck:Foo` and `deck:Foobar` share nothing. Stops at a
    term that opens or closes a group or is a bare `-`, since splitting there would cut
    through the query's structure. A query with a top-level OR gets no prefix at all:
    Anki binds AND tighter than OR, so `deck:A x OR y` means `(deck:A x) OR y`, and
    showing `x OR y` under a `deck:A` header would misstate it. Every query must keep at
    least one term of its own after the prefix."""
    if len(queries) < 2:
        return ""
    split = [_TOKEN_RE.findall(q) for q in queries]
    for tokens in split:
        depth = 0
        for tok in tokens:
            if depth == 0 and tok.lower() == "or":
                return ""
            depth += tok.count("(") - tok.count(")")

    common: List[str] = []
    for column in zip(*split):
        tok = column[0]
        if any(t != tok for t in column):
            break
        if "(" in tok or ")" in tok or tok == "-":
            break
        common.append(tok)
    shortest = min(len(t) for t in split)
    while common and len(common) >= shortest:
        common.pop()
    return " ".join(common)


def strip_prefix(query: str, prefix: str) -> str:
    if not prefix:
        return query
    tokens = _TOKEN_RE.findall(query)
    n = len(_TOKEN_RE.findall(prefix))
    return " ".join(tokens[n:])


def _epoch_ms(timestamp: str) -> Optional[int]:
    try:
        return int(datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _is_empty(entry: PrioritySearchSummary, is_mix: bool) -> bool:
    return entry.refined_match_count == 0 if is_mix else entry.kept_count == 0


def _queue_range(entry: PrioritySearchSummary) -> str:
    if entry.final_start_index is None or entry.kept_count <= 0:
        return ""
    first = entry.final_start_index + 1
    if entry.turns > 1 and entry.later_start is not None and entry.last_index is not None:
        return _cycle_range(entry)
    last = entry.final_start_index + entry.kept_count
    return str(first) if first == last else f"{first}–{last}"


def _span(first: int, last: int) -> str:
    return str(first) if first == last else f"{first}–{last}"


def _cycle_range(entry: PrioritySearchSummary) -> str:
    """The first-turn block, then the stretch its later turns share with the other
    searches still taking turns. A search that ended up in one solid block anyway
    reads as a plain range."""
    first = entry.final_start_index + 1
    last = entry.last_index + 1
    if last - first + 1 == entry.kept_count:
        return _span(first, last)
    first_end = entry.final_start_index + entry.first_turn_count
    later = entry.later_start + 1
    if later == first_end + 1:
        return f"{_span(first, last)} ↻"
    return f"{_span(first, first_end)}, {_span(later, last)} ↻"


def _cycle_tip(entry: PrioritySearchSummary) -> str:
    """Hover text for a search whose cards are spread over several turns."""
    if entry.turns <= 1 or entry.final_start_index is None or entry.last_index is None:
        return ""
    first = entry.final_start_index + 1
    first_end = entry.final_start_index + entry.first_turn_count
    block = str(first) if first == first_end else f"{first}–{first_end}"
    more = entry.turns - 1
    return (f"First turn {block}, then {more} more turn{'s' if more != 1 else ''}, "
            f"last card at {entry.last_index + 1}")


def _previous_kept(report: ReorderReport, previous: Optional[ReorderReport]) -> Dict[str, int]:
    """Kept counts from the previous run, keyed by query text so an edited config (a
    search inserted or removed) still lines each search up with its own history."""
    if previous is None or previous.mode != report.mode or report.mode == "mix":
        return {}
    return {e.query: e.kept_count for e in previous.entries}


class _Flags:
    """Which optional parts of the page apply to this report. Anything tied to a
    setting that is off, or a count that is zero everywhere, stays hidden."""

    def __init__(self, report: ReorderReport) -> None:
        entries = report.entries
        self.is_mix = report.mode == "mix"
        self.cutoff = report.priority_cutoff is not None
        self.global_limit = report.global_priority_limit is not None
        # A cycle config without any limit= runs, and renders, exactly like sequential.
        self.is_cycle = report.mode == "cycle" and any(e.limit is not None for e in entries)
        # Cycling spends limit= per turn, so only limit=0 can leave a card over it.
        self.any_limit = self.global_limit or (
            any(e.limit_discarded for e in entries) if self.is_cycle
            else any(e.limit is not None for e in entries))
        self.any_overlap = any(e.overlap_count for e in entries)


def _header(report: ReorderReport, reordering: bool) -> str:
    kpis = (
        f'<div class="kpi"><span class="v">{_fmt(report.total_priority_kept)}</span>'
        f'<span class="l">prioritized</span></div>'
        f'<div class="kpi"><span class="v">{_fmt(report.total_normal)}</span>'
        f'<span class="l">normal</span></div>'
    )
    trigger = _TRIGGER_TEXT.get(report.trigger, "")
    placed = report.total_priority_kept + report.total_normal
    notes = [_esc(report.timestamp)]
    if report.total_repositioned == 0 and placed > 0:
        notes.append("already in order, nothing moved")
    epoch = _epoch_ms(report.timestamp)
    when = (
        f'<b>Reordered <span id="ago" data-epoch="{epoch}">at {_esc(report.timestamp)}</span>'
        f'{", " + trigger if trigger else ""}</b>'
        if epoch is not None
        else f"<b>Reordered{' ' + trigger if trigger else ''}</b>"
    )
    return f"""
<div class="top">
  <div class="kpis">{kpis}</div>
  <div class="right">
    <div class="when">{when}<span class="sub">{" · ".join(notes)}</span>{_new_limit_notes(report)}</div>
    <div class="actions">
      <button type="button" onclick="pycmd('config')">Edit config</button>
      {_reorder_button(reordering)}
    </div>
  </div>
</div>"""


def _new_limit_note(c: NewLimitChange) -> str:
    deck = _esc(c.deck)
    if c.status == "raised":
        return f"{deck}: {c.baseline} → {c.target} new cards today"
    if c.status == "frozen":
        return f"{deck}: {c.target} new cards today, kept since studying started"
    if c.status == "hand_set":
        return f"{deck}: Today-only limit of {c.target} was set by hand, left alone"
    if c.status == "cleared":
        return f"{deck}: back to {c.baseline} new cards today"
    if c.status == "not_needed":
        return f"{deck}: queue fits in {c.baseline} new cards today"
    return f'no deck named "{deck}" for today_new_limit'


def _new_limit_notes(report: ReorderReport) -> str:
    if not report.new_limit_enabled:
        return ""
    if not report.new_limit_changes:
        return '<span class="sub">today_new_limit is on but no deck is set</span>'
    return "".join(f'<span class="sub">{_new_limit_note(c)}</span>'
                   for c in report.new_limit_changes)


def _reorder_button(reordering: bool) -> str:
    if reordering:
        return '<button type="button" class="primary" id="reorder" disabled>Reordering...</button>'
    return '<button type="button" class="primary" id="reorder" onclick="runReorder()">Run reorder</button>'


def _segments_from_entries(report: ReorderReport) -> List[QueueSegment]:
    """The sequential layout, for a report that recorded no segments of its own."""
    segments = []
    pos = 0
    for e in report.entries:
        if e.kept_count > 0:
            segments.append(QueueSegment(kind="search", start=pos, count=e.kept_count, search=e.index))
            pos += e.kept_count
    if report.promoted_count > 0:
        segments.append(QueueSegment(kind="promoted", start=pos, count=report.promoted_count))
    return segments


def _positions(seg: QueueSegment) -> str:
    first, last = seg.start + 1, seg.start + seg.count
    return str(first) if first == last else f"{first}–{last}"


def _queue_bar(report: ReorderReport) -> str:
    total = report.total_priority_kept
    if total <= 0:
        return ""
    queries = {e.index: e.query for e in report.entries}
    segments = []
    kinds = set()
    for seg in report.queue_segments or _segments_from_entries(report):
        kinds.add(seg.kind)
        wide = seg.count / total >= _SEGMENT_LABEL_SHARE
        if seg.kind == "promoted":
            segments.append(
                f'<div class="seg promo" style="flex:{seg.count}" '
                f'title="Promoted tier: {_fmt(seg.count)} normal cards '
                f'lifted by normal_prioritization, positions {_positions(seg)}"></div>'
            )
        elif seg.kind == "cycle":
            order = sorted(seg.cycled)
            lines = "".join(f"\n[{i + 1}] {_fmt(seg.cycled[i])} cards" for i in order)
            tip = f"Searches taking turns, positions {_positions(seg)}{lines}"
            label = "·".join(str(i + 1) for i in order) if wide else ""
            row = f' data-row="{order[0]}"' if order else ""
            segments.append(
                f'<div class="seg cyc" style="flex:{seg.count}"{row} '
                f'title="{_esc(tip)}">{_esc(label)}</div>'
            )
        else:
            tip = (f"[{seg.search + 1}] {queries.get(seg.search, '')}\n"
                   f"{_fmt(seg.count)} cards, positions {_positions(seg)}")
            label = str(seg.search + 1) if wide else ""
            segments.append(
                f'<div class="seg" style="flex:{seg.count}" data-row="{seg.search}" '
                f'title="{_esc(tip)}">{label}</div>'
            )
    legend = ""
    if kinds & {"promoted", "cycle"}:
        items = ['<span><i class="sw k"></i>searches</span>']
        if "cycle" in kinds:
            items.append('<span><i class="sw cyc"></i>taking turns</span>')
        if "promoted" in kinds:
            items.append('<span><i class="sw p"></i>promoted tier</span>')
        legend = f'<span class="legend">{"".join(items)}</span>'
    return f"""
<div class="qbar-wrap">
  <div class="qlabel"><span>Queue, first {_fmt(total)} cards</span>{legend}</div>
  <div class="qrow">
    <div class="qbar">{"".join(segments)}</div>
    <span class="qnormal">then {_fmt(report.total_normal)} normal</span>
  </div>
</div>"""


def _legend(flags: _Flags) -> str:
    if flags.is_mix:
        return '<div class="legend">Mix mode: all matches share one sorted pool</div>'
    items = ['<span>Cycle mode: searches take turns, limit= cards per turn</span>'] if flags.is_cycle else []
    items.append('<span><i class="sw k"></i>kept</span>')
    if flags.any_overlap:
        items.append('<span><i class="sw e"></i>taken by earlier search</span>')
    if flags.any_limit:
        items.append('<span><i class="sw l"></i>over limit</span>')
    if flags.cutoff:
        items.append('<span><i class="sw c"></i>below cutoff</span>')
    return f'<div class="legend">{"".join(items)}</div>'


def _loss_bar(e: PrioritySearchSummary) -> str:
    matched = e.refined_match_count
    if matched <= 0:
        return ""
    parts = [
        (e.kept_count, "k"),
        (e.overlap_count, "e"),
        (e.limit_discarded + e.global_limit_discarded, "l"),
        (e.cutoff_dropped, "c"),
    ]
    segs = "".join(
        f'<i class="{cls}" style="width:{count / matched * 100:.3f}%"></i>'
        for count, cls in parts if count > 0
    )
    return f'<div class="lbar" title="{_esc(_loss_text(e))}">{segs}</div>'


def _loss_parts(e: PrioritySearchSummary, report: ReorderReport, rng: str) -> List[str]:
    parts = []
    if e.kept_count:
        parts.append(f'<i class="sw k"></i><b>{_fmt(e.kept_count)}</b> kept'
                     + (f" ({rng})" if rng else ""))
    if e.overlap_count:
        parts.append(f'<i class="sw e"></i><b>{_fmt(e.overlap_count)}</b> taken earlier')
    if e.limit_discarded:
        parts.append(f'<i class="sw l"></i><b>{_fmt(e.limit_discarded)}</b> over limit={e.limit}')
    if e.global_limit_discarded:
        parts.append(f'<i class="sw l"></i><b>{_fmt(e.global_limit_discarded)}</b> '
                     f'over priority_limit={report.global_priority_limit}')
    if e.cutoff_dropped:
        parts.append(f'<i class="sw c"></i><b>{_fmt(e.cutoff_dropped)}</b> below cutoff')
    return parts


def _loss_text(e: PrioritySearchSummary) -> str:
    bits = [f"{e.kept_count} kept"]
    if e.overlap_count:
        bits.append(f"{e.overlap_count} taken earlier")
    discarded = e.limit_discarded + e.global_limit_discarded
    if discarded:
        bits.append(f"{discarded} over limit")
    if e.cutoff_dropped:
        bits.append(f"{e.cutoff_dropped} below cutoff")
    return f"{e.refined_match_count} matched: " + ", ".join(bits)


def _matched_text(e: PrioritySearchSummary) -> str:
    if e.has_custom_rules and e.raw_match_count != e.refined_match_count:
        return f"<b>{_fmt(e.refined_match_count)}</b> of {_fmt(e.raw_match_count)} matched"
    return f"<b>{_fmt(e.refined_match_count)}</b> matched"


def _detail(e: PrioritySearchSummary, report: ReorderReport, flags: _Flags, span: int) -> str:
    fragments = [_matched_text(e)]
    buttons = []
    if not flags.is_mix:
        fragments += _loss_parts(e, report, _queue_range(e))
        for kind, label, ids in (
            ("kept", "Kept", e.kept_note_ids),
            ("discarded", "Over limit", e.discarded_note_ids),
            ("cutoff", "Below cutoff", e.cutoff_note_ids),
        ):
            if ids:
                buttons.append(
                    f'<button type="button" onclick="pycmd(\'browse:{kind}:{e.index}\')">'
                    f"{label} ({_fmt(len(ids))})</button>"
                )
    flow = "".join(f"<span>{f}</span>" for f in fragments)
    btns = f'<div class="btns">{"".join(buttons)}</div>' if buttons else ""
    return (
        f'<tr class="detail" id="detail-{e.index}"><td colspan="{span}">'
        f'<div class="flow">{flow}</div>{btns}</td></tr>'
    )


def _row(
    e: PrioritySearchSummary,
    report: ReorderReport,
    flags: _Flags,
    prefix: str,
    dark: bool,
    prev_kept: Dict[str, int],
    open_rows: Set[int],
    span: int,
) -> str:
    empty = _is_empty(e, flags.is_mix)
    is_open = (not empty) and e.index in open_rows
    query_html = colorize_query_html(strip_prefix(e.query, prefix), dark=dark)

    cells = [
        f'<td class="caret">{"" if empty else ("▼" if is_open else "▶")}</td>',
        f'<td class="idx">{e.index + 1}</td>',
    ]
    sub = "" if (empty or flags.is_mix) else _loss_bar(e)
    cells.append(f'<td class="q"><div>{query_html}</div>{sub}</td>')
    if not flags.is_mix:
        delta = ""
        if e.query in prev_kept:
            d = e.kept_count - prev_kept[e.query]
            if d:
                sign, cls = ("+", "up") if d > 0 else ("−", "down")
                delta = f'<span class="delta {cls}" title="vs. the previous reorder">{sign}{abs(d)}</span>'
        cells.append(f'<td class="n"><span class="kept">{_fmt(e.kept_count)}</span>{delta}</td>')
    cells.append(f'<td class="n sub">{_fmt(e.refined_match_count)}</td>')
    if not flags.is_mix:
        tip = _cycle_tip(e)
        title = f' title="{_esc(tip)}"' if tip else ""
        cells.append(f'<td class="pos"{title}>{_queue_range(e)}</td>')

    if empty:
        return f'<tr class="row empty" id="row-{e.index}">{"".join(cells)}</tr>'
    classes = "row open" if is_open else "row"
    row = (
        f'<tr class="{classes}" id="row-{e.index}" data-i="{e.index}" tabindex="0" '
        f'aria-expanded="{"true" if is_open else "false"}">{"".join(cells)}</tr>'
    )
    detail = _detail(e, report, flags, span)
    if not is_open:
        detail = detail.replace('<tr class="detail"', '<tr class="detail" hidden', 1)
    return row + detail


def _table(report: ReorderReport, flags: _Flags, dark: bool,
           previous: Optional[ReorderReport], open_rows: Set[int]) -> str:
    prefix = shared_prefix([e.query for e in report.entries])
    prefix_html = (
        f' <span class="thprefix">· {_esc(prefix)}</span>' if prefix else ""
    )
    head = ["<th></th>", "<th>#</th>", f"<th>Search{prefix_html}</th>"]
    if not flags.is_mix:
        head.append('<th class="n">Kept</th>')
    head.append('<th class="n">Matched</th>')
    if not flags.is_mix:
        head.append("<th>Queue</th>")
    span = len(head)
    prev_kept = _previous_kept(report, previous)
    rows = "".join(
        _row(e, report, flags, prefix, dark, prev_kept, open_rows, span)
        for e in report.entries
    )
    return f'<table class="st"><thead><tr>{"".join(head)}</tr></thead><tbody>{rows}</tbody></table>'


def render_summary(
    report: Optional[ReorderReport],
    previous: Optional[ReorderReport] = None,
    *,
    dark: bool = False,
    open_rows: Iterable[int] = (),
    reordering: bool = False,
    font_px: Optional[float] = None,
) -> str:
    """The complete page body (markup, style and script) for stdHtml().

    ``font_px`` sets the base text size. Without it the webview falls back to the
    browser default of 16px, a size larger than the 9pt Qt font the rest of Anki's
    dialogs use."""
    if report is None:
        body = f"""
<div class="blank">
  <p>No reorder has run yet this session.</p>
  {_reorder_button(reordering)}
</div>"""
    elif not report.entries:
        body = _header(report, reordering) + (
            '<div class="blank"><p>No priority searches are configured.</p>'
            "<button type=\"button\" onclick=\"pycmd('config')\">Edit config</button></div>"
        )
    else:
        flags = _Flags(report)
        top = _header(report, reordering)
        if not flags.is_mix:
            top += _queue_bar(report)
        top += _legend(flags)
        body = (
            f'<div class="pane">{top}</div>'
            f'<div class="scroller" id="scroller">'
            f"{_table(report, flags, dark, previous, set(open_rows))}</div>"
        )
    size = f"body {{ font-size: {font_px:.2f}px; }}" if font_px else ""
    return f"<style>{_CSS}{size}</style><div class=\"page\">{body}</div><script>{_JS}</script>"


_CSS = """
:root {
  --pr-fg: var(--fg, #202020);
  --pr-sub: var(--fg-subtle, #666);
  --pr-line: var(--border-subtle, #e4e4e4);
  --pr-hover: rgba(0, 0, 0, 0.05);
  --pr-btn: var(--button-bg, #fdfdfd);
  --pr-btn-line: var(--border, #c4c4c4);
  --pr-focus: var(--border-focus, #3b82f6);
  --pr-k: #2f6fd6;
  --pr-k2: #6c9be6;
  --pr-e: #b0b6bf;
  --pr-l: #b86e0b;
  --pr-c: #c0392b;
  --pr-p: #148a77;
  --pr-up: #1c7c3c;
  --pr-down: #b23a2c;
}
html.night-mode {
  --pr-fg: var(--fg, #eee);
  --pr-sub: var(--fg-subtle, #a0a0a0);
  --pr-line: var(--border-subtle, #3a3a3a);
  --pr-hover: rgba(255, 255, 255, 0.06);
  --pr-btn: var(--button-bg, #3a3a3a);
  --pr-btn-line: var(--border, #555);
  --pr-k: #5c9dff;
  --pr-k2: #3e6fb8;
  --pr-e: #5b5f66;
  --pr-l: #e0a33c;
  --pr-c: #ff6b5b;
  --pr-p: #3cc4a8;
  --pr-up: #5fd08a;
  --pr-down: #ff7b6b;
}
html, body { height: 100%; margin: 0; padding: 0; }
body { color: var(--pr-fg); overflow: hidden; }
.page { display: flex; flex-direction: column; height: 100%; }
.pane { padding: 12px 14px 8px; display: flex; flex-direction: column; gap: 10px;
        border-bottom: 1px solid var(--pr-line); }
.scroller { flex: 1; min-height: 0; overflow: auto; }
.sub { color: var(--pr-sub); }

button { font: inherit; color: var(--pr-fg); background: var(--pr-btn);
         border: 1px solid var(--pr-btn-line); border-radius: 5px; padding: 3px 12px;
         cursor: pointer; white-space: nowrap; }
button:hover:not(:disabled) { filter: brightness(0.96); }
html.night-mode button:hover:not(:disabled) { filter: brightness(1.15); }
button.primary { background: var(--pr-k); border-color: var(--pr-k); color: #fff; }
button:disabled { opacity: 0.6; cursor: default; }
button:focus-visible, tr.row:focus-visible, .seg:focus-visible {
  outline: 2px solid var(--pr-focus); outline-offset: -2px; }

.top { display: flex; justify-content: space-between; align-items: center; gap: 14px;
       flex-wrap: wrap; }
.kpis { display: flex; gap: 22px; }
.kpi { display: flex; flex-direction: column; }
.kpi .v { font-size: 1.5em; font-weight: 600; line-height: 1.2;
          font-variant-numeric: tabular-nums; }
.kpi .l { color: var(--pr-sub); font-size: 0.9em; }
.right { display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
         justify-content: flex-end; }
.when { display: flex; flex-direction: column; text-align: right; }
.when .sub { font-size: 0.9em; }
.actions { display: flex; gap: 6px; }

.qbar-wrap { display: flex; flex-direction: column; gap: 4px; }
.qlabel { display: flex; justify-content: space-between; font-size: 0.9em;
          color: var(--pr-sub); }
.qrow { display: flex; align-items: center; gap: 8px; }
.qbar { flex: 1; display: flex; height: 20px; border-radius: 4px; overflow: hidden;
        border: 1px solid var(--pr-line); }
.seg { height: 100%; border-right: 1px solid var(--canvas, #fff); cursor: pointer;
       display: flex; align-items: center; justify-content: center; overflow: hidden;
       color: #fff; font-size: 0.75em; font-variant-numeric: tabular-nums; }
.seg:nth-child(odd) { background: var(--pr-k); }
.seg:nth-child(even) { background: var(--pr-k2); }
.seg.promo { background: var(--pr-p); }
.seg.cyc, .sw.cyc { background: repeating-linear-gradient(135deg, var(--pr-k) 0 5px, var(--pr-k2) 5px 10px); }
.seg:hover { filter: brightness(1.2); }
.qnormal { font-size: 0.9em; color: var(--pr-sub); white-space: nowrap; }

.legend { display: flex; gap: 12px; flex-wrap: wrap; font-size: 0.85em; color: var(--pr-sub); }
.sw { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 5px; }
.sw.k, .lbar .k { background: var(--pr-k); }
.sw.e, .lbar .e { background: var(--pr-e); }
.sw.l, .lbar .l { background: var(--pr-l); }
.sw.c, .lbar .c { background: var(--pr-c); }
.sw.p { background: var(--pr-p); }

table.st { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.st th { position: sticky; top: 0; z-index: 1; background: var(--canvas, #fff);
         text-align: left; font-weight: normal; color: var(--pr-sub); font-size: 0.9em;
         padding: 6px 8px; border-bottom: 1px solid var(--pr-line); white-space: nowrap; }
.thprefix { display: inline-block; max-width: 36ch; overflow: hidden; text-overflow: ellipsis;
            white-space: nowrap; vertical-align: top; }
.st td { padding: 5px 8px; border-bottom: 1px solid var(--pr-line); vertical-align: middle; }
.st td.caret { width: 1%; color: var(--pr-sub); font-size: 0.7em; padding-right: 0; }
.st td.idx { width: 1%; color: var(--pr-sub); }
.st td.q { width: 100%; overflow-wrap: break-word; }
.st td.n, .st td.pos { width: 1%; white-space: nowrap; }
.st td.pos { color: var(--pr-sub); }
.st tr.row { cursor: pointer; }
.st tr.row:hover td, .st tr.row:hover + tr.detail td { background: var(--pr-hover); }
.st tr.row.open td { border-bottom: 0; }
.st tr.row.empty { cursor: default; }
.st tr.row.empty td { color: var(--pr-sub); font-size: 0.9em; padding-top: 1px;
                      padding-bottom: 1px; }
.st tr.row.empty:hover td { background: none; }
.st tr.row.empty td.q span { opacity: 0.7; }
.kept { font-weight: 600; }
.delta { font-size: 0.8em; font-weight: 600; margin-left: 4px; }
.delta.up { color: var(--pr-up); }
.delta.down { color: var(--pr-down); }
.lbar { display: flex; height: 5px; max-width: 320px; margin-top: 3px; border-radius: 2px;
        overflow: hidden; background: var(--pr-line); }
.lbar i { display: block; height: 100%; }
tr.detail td { padding: 2px 14px 10px 46px; }
.flow { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 0.9em; margin-bottom: 8px; }
.btns { display: flex; gap: 6px; flex-wrap: wrap; }

.blank { flex: 1; display: flex; flex-direction: column; align-items: center;
         justify-content: center; gap: 12px; color: var(--pr-sub); }
"""

_JS = """
(function () {
  function toggle(tr) {
    var i = tr.dataset.i;
    var detail = document.getElementById('detail-' + i);
    if (!detail) return;
    var open = detail.hidden;
    detail.hidden = !open;
    tr.classList.toggle('open', open);
    tr.setAttribute('aria-expanded', open ? 'true' : 'false');
    tr.querySelector('.caret').textContent = open ? '\\u25BC' : '\\u25B6';
    pycmd('toggle:' + i);
  }
  document.addEventListener('click', function (ev) {
    if (ev.target.closest('button')) return;
    var seg = ev.target.closest('.seg[data-row]');
    if (seg) {
      var row = document.getElementById('row-' + seg.dataset.row);
      if (row) {
        if (!row.classList.contains('open')) toggle(row);
        row.scrollIntoView({block: 'center'});
        row.focus();
      }
      return;
    }
    var tr = ev.target.closest('tr.row[data-i]');
    if (tr) toggle(tr);
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    var tr = ev.target.closest && ev.target.closest('tr.row[data-i]');
    if (tr && ev.target === tr) { ev.preventDefault(); toggle(tr); }
  });
  // The header prefix gets a tooltip only when the ellipsis actually cut it off.
  document.querySelectorAll('.thprefix').forEach(function (el) {
    el.addEventListener('mouseenter', function () {
      el.title = el.scrollWidth > el.clientWidth ? el.textContent.slice(2) : '';
    });
  });
  var ago = document.getElementById('ago');
  function tick() {
    if (!ago) return;
    var mins = Math.floor((Date.now() - Number(ago.dataset.epoch)) / 60000);
    ago.textContent = mins < 1 ? 'just now'
      : mins < 60 ? mins + ' min ago'
      : mins < 1440 ? Math.floor(mins / 60) + ' h ago'
      : 'on ' + new Date(Number(ago.dataset.epoch)).toLocaleDateString();
  }
  tick();
  setInterval(tick, 30000);
  window.runReorder = function () {
    var b = document.getElementById('reorder');
    if (b) { b.disabled = true; b.textContent = 'Reordering...'; }
    pycmd('reorder');
  };
})();
"""
