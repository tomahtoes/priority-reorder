from typing import List, Optional

from aqt import mw, dialogs # type: ignore
from aqt.operations import CollectionOp # type: ignore
from aqt.qt import ( # type: ignore
    QColor,
    QDialog,
    QFont,
    QFontMetrics,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPainter,
    QPalette,
    QPixmap,
    QPointF,
    QPolygonF,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    Qt,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
    qconnect,
)
from aqt.theme import theme_manager # type: ignore
from aqt.utils import showInfo # type: ignore

from .config_manager import migrate_config_in_place
from .reorder_log import PrioritySearchSummary, ReorderReport, get_last_report
from .reorderer import run_reorder
from .search_colors import colorize_query_html


def _is_dark() -> bool:
    try:
        return bool(theme_manager.night_mode)
    except Exception:
        return False


def _muted_color() -> str:
    return "#a0a0a0" if _is_dark() else "#6b6b6b"


def _accent_red() -> str:
    return "#ff6b5b" if _is_dark() else "#c0392b"


def _card_bg() -> str:
    # Anki's --canvas-glass: the translucent container fill the main screen's
    # deck list uses; blends with the dialog background underneath.
    return "rgba(54, 54, 54, 0.4)" if _is_dark() else "rgba(255, 255, 255, 0.4)"


def _card_border() -> str:
    # Anki's --border-subtle: the deck-list container's hairline outline.
    return "#252525" if _is_dark() else "#e4e4e4"


def _hover_bg() -> str:
    # Slightly stronger tint than the card fill so hover reads both on the
    # bare window (compact rows) and on the glass fill (expanded cards).
    return "rgba(70, 70, 70, 0.5)" if _is_dark() else "rgba(0, 0, 0, 0.05)"


def _nid_search(note_ids: List[int]) -> str:
    return "nid:" + ",".join(str(n) for n in note_ids) if note_ids else ""


def _open_in_browser(note_ids: List[int]) -> None:
    if not note_ids:
        return
    browser = dialogs.open("Browser", mw)
    browser.search_for(_nid_search(note_ids))


class ClickableWidget(QWidget):
    """A container that emits ``clicked`` on left mouse press. Child QLabels
    ignore mouse events, so a click anywhere on the row lands here."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _arrow_pixmap(expanded: bool, color: str, size: int, dpr: float) -> QPixmap:
    """A crisp antialiased collapse arrow. Font glyphs (▶/►) come from
    inconsistent symbol-font fallbacks and render jagged at small sizes, so
    the triangle is painted directly instead."""
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    if expanded:
        pts = [(0.08, 0.25), (0.92, 0.25), (0.5, 0.82)]
    else:
        pts = [(0.25, 0.08), (0.25, 0.92), (0.82, 0.5)]
    p.drawPolygon(QPolygonF([QPointF(x * size, y * size) for x, y in pts]))
    p.end()
    return pm


class SummaryCell(QWidget):
    """A single label+value pair laid out tightly: dimmed label, value
    emphasized by color only."""

    def __init__(self, label: str, value: str, *, accent: Optional[str] = None, dim: bool = False, point_size: Optional[int] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {_muted_color()};")
        if point_size and point_size > 0:
            lf = lbl.font()
            lf.setPointSize(point_size)
            lbl.setFont(lf)
        layout.addWidget(lbl)

        val = QLabel(value)
        if point_size and point_size > 0:
            vf = val.font()
            vf.setPointSize(point_size)
            val.setFont(vf)
        if accent:
            val.setStyleSheet(f"color: {accent};")
        elif dim:
            # Zero counts: muted so meaningful numbers stand out.
            val.setStyleSheet(f"color: {_muted_color()};")
        layout.addWidget(val)


class SearchCard(QFrame):
    """Collapsible card for a single priority search."""

    def __init__(
        self,
        entry: PrioritySearchSummary,
        mode: str,
        *,
        cutoff_active: bool,
        global_limit_active: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self._cutoff_active = cutoff_active
        self._global_limit_active = global_limit_active

        self.setObjectName("searchCard")

        # No layout margins: the card's inset lives in the header's own margins
        # and the body's margins, so the whole header band (full width, up to the
        # card edge) is part of the clickable header — not dead margin space.
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        # Header: collapse toggle + title. Start collapsed when nothing was kept
        # (in mix mode, kept_count isn't meaningful so fall back to matches).
        if mode == "mix":
            start_expanded = entry.refined_match_count > 0
        else:
            start_expanded = entry.kept_count > 0
        # A search that contributed nothing (collapsed by default) carries no
        # standout info: render it as a compact, muted one-liner while collapsed
        # and restore the full card on expand.
        self._dimmed = not start_expanded
        self._expanded = start_expanded
        # Header row: a painted arrow + rich-text title inside a clickable
        # container, so the whole band toggles. The header's inset lives in
        # this layout's margins (swapped per state in _apply_chrome).
        self._header = ClickableWidget()
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_layout = QHBoxLayout(self._header)
        self._header_layout.setSpacing(8)
        self._arrow_lbl = QLabel()
        self._header_layout.addWidget(self._arrow_lbl)
        self._title_lbl = QLabel()
        self._title_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._header_layout.addWidget(self._title_lbl, 1)
        self._base_point_size = self._title_lbl.font().pointSize()
        # Colored terms for the active/expanded header; the [index] prefix
        # stays default text color. The muted variant keeps the term colors
        # but blends them toward gray, shown when a dimmed row is collapsed.
        self._query_html = colorize_query_html(entry.query, dark=_is_dark())
        self._query_html_muted = colorize_query_html(
            entry.query, dark=_is_dark(), mute_toward=_muted_color()
        )
        self._prefix_html = f"[{entry.index + 1}]&nbsp;&nbsp;&nbsp;"
        qconnect(self._header.clicked, self._toggle)
        self._outer.addWidget(self._header)

        # Body. Add it to the card's layout (reparenting it) BEFORE making it
        # visible: a parentless widget that is shown — even briefly — appears as
        # its own top-level window, i.e. a flashing popup. Its side/bottom margins
        # replace the old layout margins; the gap above it comes from the header's
        # bottom padding.
        self._body = QWidget()
        self._outer.addWidget(self._body)
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(10, 0, 10, 6)
        body_layout.setSpacing(6)
        self._populate_body(body_layout, entry, mode)
        self._body.setVisible(start_expanded)

        # Drop shadow matching the deck-list container on the main screen
        # (".fancy table" in Anki's deckbrowser.css). Disabled while a dimmed
        # row is compact: with a transparent background the shadow would
        # outline the text itself.
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setColor(QColor(20, 20, 20, 60))
        self._shadow.setBlurRadius(6)
        self._shadow.setOffset(0, 2)
        self.setGraphicsEffect(self._shadow)

        self._apply_chrome(start_expanded)
        self._update_header_text(start_expanded)

    def _apply_chrome(self, expanded: bool) -> None:
        """Style the card frame and header padding. Active cards are rounded
        glass-filled containers with a hairline border and drop shadow, like
        the main screen's deck list; empty searches drop all of that and
        shrink to a bare one-liner while collapsed, restoring the full card
        on expand."""
        compact = self._dimmed and not expanded
        if compact:
            self.setStyleSheet(
                "#searchCard { background-color: transparent; border: none; }"
                f"#searchCard:hover {{ background-color: {_hover_bg()}; border-radius: 6px; }}"
            )
            self._header_layout.setContentsMargins(10, 1, 10, 1)
        else:
            self.setStyleSheet(
                f"#searchCard {{"
                f"  background-color: {_card_bg()};"
                f"  border: 1px solid {_card_border()};"
                f"  border-radius: 6px;"
                f"}}"
                f"#searchCard:hover {{ background-color: {_hover_bg()}; }}"
            )
            self._header_layout.setContentsMargins(10, 6, 10, 6)
        self._shadow.setEnabled(not compact)
        font = self._title_lbl.font()
        if self._base_point_size > 0:
            font.setPointSize(self._base_point_size - 1 if compact else self._base_point_size)
        self._title_lbl.setFont(font)

    def _update_header_text(self, expanded: bool) -> None:
        compact = self._dimmed and not expanded
        arrow_color = (
            _muted_color()
            if compact
            else self.palette().color(QPalette.ColorRole.WindowText).name()
        )
        # Size the arrow to the title's current font; a fixed-width label
        # keeps the text from shifting between the two orientations.
        fm = QFontMetrics(self._title_lbl.font())
        size = max(8, round(fm.ascent() * 0.9))
        self._arrow_lbl.setFixedWidth(size)
        self._arrow_lbl.setPixmap(
            _arrow_pixmap(expanded, arrow_color, size, self.devicePixelRatioF())
        )
        query_html = self._query_html_muted if compact else self._query_html
        text = f"{self._prefix_html}{query_html}"
        if compact:
            # Mute the whole line — [index] included, arrow via arrow_color —
            # so an empty row reads as inactive at a glance; the query's
            # blended term colors (inner spans) still show through.
            text = f'<span style="color:{_muted_color()}">{text}</span>'
        self._title_lbl.setText(text)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._body.setVisible(expanded)
        if self._dimmed:
            self._apply_chrome(expanded)
        self._update_header_text(expanded)

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _populate_body(self, body_layout: QVBoxLayout, entry: PrioritySearchSummary, mode: str) -> None:
        is_mix = mode == "mix"

        # Stats and the browser-button labels both use the card's base font; the
        # button box height is keyed to that same font (below) so the text sits
        # comfortably inside it.
        base_pt = QFont().pointSize()
        stats_pt = base_pt
        btn_pt = base_pt

        cells: List[SummaryCell] = []

        def add_cell(label: str, value: str, accent: Optional[str] = None, dim: bool = False) -> None:
            cells.append(SummaryCell(label, value, accent=accent, dim=dim, point_size=stats_pt))

        if not is_mix:
            add_cell("kept", str(entry.kept_count), dim=entry.kept_count == 0)

        add_cell("matched", str(entry.refined_match_count), dim=entry.refined_match_count == 0)
        if entry.has_custom_rules and entry.raw_match_count != entry.refined_match_count:
            add_cell("raw", str(entry.raw_match_count), dim=entry.raw_match_count == 0)

        if not is_mix:
            can_be_discarded = entry.limit is not None or self._global_limit_active
            if can_be_discarded:
                discarded_total = entry.limit_discarded + entry.global_limit_discarded
                accent = _accent_red() if discarded_total > 0 else None
                limit_str = f" / limit {entry.limit}" if entry.limit is not None else ""
                add_cell("discarded", f"{discarded_total}{limit_str}", accent=accent, dim=discarded_total == 0)

            if self._cutoff_active:
                add_cell("cutoff-dropped", str(entry.cutoff_dropped), dim=entry.cutoff_dropped == 0)

            if entry.final_start_index is not None:
                add_cell("starts at", str(entry.final_start_index))

        # Stats on the left; browser buttons anchored to the right of the same line.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for i, cell in enumerate(cells):
            if i > 0:
                sep = QLabel("·")
                sep.setStyleSheet(f"color: {_muted_color()};")
                sep.setContentsMargins(10, 0, 10, 0)
                if stats_pt > 0:
                    sf = sep.font()
                    sf.setPointSize(stats_pt)
                    sep.setFont(sf)
                row.addWidget(sep)
            row.addWidget(cell)
        row.addStretch(1)

        if not is_mix:
            buttons = [
                b for b in (
                    self._make_browser_button("View kept", entry.kept_note_ids, btn_pt, base_pt),
                    self._make_browser_button("View discarded", entry.discarded_note_ids, btn_pt, base_pt),
                )
                if b is not None
            ]
            for i, b in enumerate(buttons):
                if i > 0:
                    row.addSpacing(6)
                row.addWidget(b)

        body_layout.addLayout(row)

        if is_mix:
            note = QLabel("(mix mode — kept/discarded combined in totals)")
            f = note.font()
            f.setItalic(True)
            if stats_pt > 0:
                f.setPointSize(stats_pt)
            note.setFont(f)
            note.setStyleSheet(f"color: {_muted_color()};")
            body_layout.addWidget(note)

    def _make_browser_button(self, label: str, note_ids: List[int], font_pt: int, height_pt: int) -> Optional[QPushButton]:
        if not note_ids:
            return None
        ids = list(note_ids)
        btn = QPushButton(f"{label} ({len(ids)})")
        btn.setStyleSheet("QPushButton { padding: 1px 12px; }")
        if font_pt > 0:
            bf = btn.font()
            bf.setPointSize(font_pt)
            btn.setFont(bf)
        # Key the height to the card's base font (passed as height_pt) so the row
        # stays a fixed, compact height regardless of the button's own larger
        # font, but never below what that font needs so the label can't clip.
        height_font = QFont()
        if height_pt > 0:
            height_font.setPointSize(height_pt)
        btn.setFixedHeight(max(
            QFontMetrics(height_font).height() + 4,
            QFontMetrics(btn.font()).height() + 2,
        ))
        qconnect(btn.clicked, lambda _=False, ids=ids: _open_in_browser(ids))
        return btn


class SummaryDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Priority Reorder Summary")
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 12, 12, 12)
        self._root.setSpacing(10)
        self._cards: List[SearchCard] = []
        self._scroll_inner: Optional[QWidget] = None
        # ConfigEditor expects parent.mgr to be the addon manager.
        self.mgr = mw.addonManager
        self.refresh()
        self.resize(self._initial_width(), self._initial_height())

    def refresh(self) -> None:
        while self._root.count():
            item = self._root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            else:
                layout = item.layout()
                if layout is not None:
                    self._clear_layout(layout)
        self._cards = []

        report = get_last_report()
        if report is None:
            self._build_empty_state()
        else:
            self._build_report(report)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_empty_state(self) -> None:
        self._root.addStretch(1)
        msg = QLabel("No reorder has run yet this session.")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = msg.font()
        f.setPointSize(f.pointSize() + 1)
        msg.setFont(f)
        self._root.addWidget(msg)
        self._root.addStretch(1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self._reorder_btn = QPushButton("Run reorder now")
        self._reorder_btn.setAutoDefault(False)
        self._reorder_btn.setDefault(False)
        self._reorder_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        qconnect(self._reorder_btn.clicked, self._run_reorder_now)
        close_row.addWidget(self._reorder_btn)
        close_btn = QPushButton("Close")
        qconnect(close_btn.clicked, self.close)
        close_row.addWidget(close_btn)
        self._root.addLayout(close_row)

    def _build_report(self, report: ReorderReport) -> None:
        # Header row: timestamp + edit config + run reorder
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        header = QLabel(f"Last reorder:  {report.timestamp}")
        f = header.font()
        f.setPointSize(f.pointSize() + 1)
        f.setBold(True)
        header.setFont(f)
        header_row.addWidget(header)

        edit_cfg_btn = QPushButton("Edit config")
        edit_cfg_btn.setAutoDefault(False)
        edit_cfg_btn.setDefault(False)
        edit_cfg_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        qconnect(edit_cfg_btn.clicked, self._open_addon_config)
        header_row.addWidget(edit_cfg_btn)

        self._reorder_btn = QPushButton("Run reorder now")
        self._reorder_btn.setAutoDefault(False)
        self._reorder_btn.setDefault(False)
        self._reorder_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        qconnect(self._reorder_btn.clicked, self._run_reorder_now)
        header_row.addWidget(self._reorder_btn)

        header_row.addStretch(1)
        self._root.addLayout(header_row)

        # At-a-glance overview of the whole report.
        if report.entries:
            n = len(report.entries)
            n_matched = sum(1 for e in report.entries if e.refined_match_count > 0)
            n_empty = n - n_matched
            overview = QLabel(
                f"{n} priority searches · {n_matched} matched"
                f" · {n_empty} empty"
                f" · {report.total_priority_kept} prioritized"
            )
            overview.setStyleSheet(f"color: {_muted_color()};")
            self._root.addWidget(overview)

        # Expand/collapse all controls
        ctrl_row = QHBoxLayout()
        expand_btn = QPushButton("Expand all")
        collapse_btn = QPushButton("Collapse all")
        for b in (expand_btn, collapse_btn):
            b.setFlat(True)
            b.setStyleSheet(
                "QPushButton { padding: 2px 8px; }"
            )
        qconnect(expand_btn.clicked, lambda: self._set_all_expanded(True))
        qconnect(collapse_btn.clicked, lambda: self._set_all_expanded(False))
        ctrl_row.addStretch(1)
        ctrl_row.addWidget(expand_btn)
        ctrl_row.addWidget(collapse_btn)
        self._root.addLayout(ctrl_row)

        # Scrollable list of search cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(6)
        # Margins keep the rows off the scrollbar (right) and leave room for
        # the cards' drop shadows, which paint outside the card rect.
        inner_layout.setContentsMargins(4, 3, 6, 6)

        if not report.entries:
            empty = QLabel("(no priority searches were configured)")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            inner_layout.addWidget(empty)
        else:
            cutoff_active = report.priority_cutoff is not None
            global_limit_active = report.global_priority_limit is not None
            for entry in report.entries:
                card = SearchCard(
                    entry,
                    report.mode,
                    cutoff_active=cutoff_active,
                    global_limit_active=global_limit_active,
                )
                inner_layout.addWidget(card)
                self._cards.append(card)
            inner_layout.addStretch(1)

        scroll.setWidget(inner)
        self._scroll_inner = inner
        self._root.addWidget(scroll, 1)

        # Footer totals + close
        footer = QHBoxLayout()
        totals = QLabel(
            f"Totals:  priority {report.total_priority_kept}    "
            f"normal {report.total_normal}    "
            f"repositioned {report.total_repositioned}"
        )
        tf = totals.font()
        tf.setBold(True)
        totals.setFont(tf)
        footer.addWidget(totals)
        footer.addStretch(1)
        close_btn = QPushButton("Close")
        qconnect(close_btn.clicked, self.close)
        footer.addWidget(close_btn)
        self._root.addLayout(footer)

    def _set_all_expanded(self, expanded: bool) -> None:
        for card in self._cards:
            card.set_expanded(expanded)

    def _open_addon_config(self) -> None:
        from aqt.addons import ConfigEditor # type: ignore

        pkg = __name__.split(".")[0]
        try:
            # Normally a no-op — the config was migrated at addon load. It matters
            # only if that write failed (read-only meta.json), where reads still
            # work but the editor would otherwise show the pre-section layout.
            conf = migrate_config_in_place(pkg)
            ConfigEditor(self, pkg, conf)
        except Exception as e:
            showInfo(f"Could not open config: {e}")

    def _run_reorder_now(self) -> None:
        self._reorder_btn.setEnabled(False)
        self._reorder_btn.setText("Reordering...")

        def on_success(_changes) -> None:
            self.refresh()

        def on_failure(err) -> None:
            self._reorder_btn.setEnabled(True)
            self._reorder_btn.setText("Run reorder now")
            showInfo(f"Error during reordering: {err}")

        op = CollectionOp(parent=self, op=run_reorder).success(on_success).failure(on_failure)
        op.run_in_background()

    def _initial_height(self) -> int:
        # QScrollArea reports a tiny sizeHint regardless of its inner content,
        # so measure the inner widget directly and add chrome around it.
        content_h = 0
        if self._scroll_inner is not None:
            self._scroll_inner.adjustSize()
            content_h = self._scroll_inner.sizeHint().height()

        # Chrome: dialog vertical margins (24) + header label (~26)
        # + ctrl row (~30) + layout spacing between sections (~30)
        # + footer row (~30) + a small buffer.
        chrome = 150
        ideal = content_h + chrome

        screen = self.screen()
        screen_h = screen.availableGeometry().height() if screen is not None else 1200
        cap = int(screen_h * 0.8)

        return max(560, min(ideal, cap))

    def _initial_width(self) -> int:
        report = get_last_report()
        if report is None or not report.entries:
            return 720

        fm = QFontMetrics(QFont())
        max_text_w = 0
        for entry in report.entries:
            text = f"[{entry.index + 1}]   {entry.query}"
            w = fm.horizontalAdvance(text)
            if w > max_text_w:
                max_text_w = w

        # Account for: dialog margins (24) + card horizontal padding (20)
        # + the arrow column (~20) + scroll-area vertical scrollbar
        # reserve (~24) + a small breathing buffer.
        chrome = 110
        return max(720, max_text_w + chrome)


_dialog: Optional[SummaryDialog] = None


def show_summary_window() -> None:
    global _dialog
    if _dialog is None:
        _dialog = SummaryDialog(parent=mw)
    else:
        _dialog.refresh()
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
