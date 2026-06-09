from typing import List, Optional

from aqt import mw, dialogs # type: ignore
from aqt.operations import CollectionOp # type: ignore
from aqt.qt import ( # type: ignore
    QDialog,
    QFont,
    QFontMetrics,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    Qt,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
    qconnect,
)
from aqt.theme import theme_manager # type: ignore
from aqt.utils import showInfo # type: ignore

from .reorder_log import PrioritySearchStats, ReorderReport, get_last_report
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
    return "#2a2a2a" if _is_dark() else "#fafafa"


def _card_border() -> str:
    return "#444" if _is_dark() else "#d0d0d0"


def _nid_search(note_ids: List[int]) -> str:
    return "nid:" + ",".join(str(n) for n in note_ids) if note_ids else ""


def _open_in_browser(note_ids: List[int]) -> None:
    if not note_ids:
        return
    browser = dialogs.open("Browser", mw)
    browser.search_for(_nid_search(note_ids))


class ClickableLabel(QLabel):
    """A QLabel that emits ``clicked`` on left mouse press (for rich-text headers
    that need to behave like a button)."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StatCell(QWidget):
    """A single label+value pair laid out tightly: dimmed label, bold value."""

    def __init__(self, label: str, value: str, *, accent: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {_muted_color()};")
        layout.addWidget(lbl)

        val = QLabel(value)
        val_font = val.font()
        val_font.setBold(True)
        val.setFont(val_font)
        if accent:
            val.setStyleSheet(f"color: {accent}; font-weight: bold;")
        layout.addWidget(val)


class SearchCard(QFrame):
    """Collapsible card for a single priority search."""

    def __init__(
        self,
        entry: PrioritySearchStats,
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
        self.setStyleSheet(
            f"#searchCard {{"
            f"  background-color: {_card_bg()};"
            f"  border: 1px solid {_card_border()};"
            f"  border-radius: 6px;"
            f"}}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(8)

        # Header: collapse toggle + title. Start collapsed when nothing was kept
        # (in mix mode, kept_count isn't meaningful so fall back to matches).
        if mode == "mix":
            start_expanded = entry.refined_match_count > 0
        else:
            start_expanded = entry.kept_count > 0
        self._expanded = start_expanded
        self._header_btn = ClickableLabel()
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.setTextFormat(Qt.TextFormat.RichText)
        header_font = self._header_btn.font()
        header_font.setBold(True)
        self._header_btn.setFont(header_font)
        self._header_btn.setStyleSheet("padding: 2px 0;")
        # Colored terms; the [index] prefix and arrow stay default text color.
        self._query_html = colorize_query_html(entry.query, dark=_is_dark())
        self._prefix_html = f"[{entry.index + 1}]&nbsp;&nbsp;&nbsp;"
        self._update_header_text(start_expanded)
        qconnect(self._header_btn.clicked, self._toggle)
        outer.addWidget(self._header_btn)

        # Body
        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        self._body.setVisible(start_expanded)
        outer.addWidget(self._body)

        self._populate_body(body_layout, entry, mode)

    def _update_header_text(self, expanded: bool) -> None:
        arrow = "▼" if expanded else "▶"
        self._header_btn.setText(
            f"{arrow}&nbsp;&nbsp;&nbsp;{self._prefix_html}{self._query_html}"
        )

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._body.setVisible(expanded)
        self._update_header_text(expanded)

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _populate_body(self, body_layout: QVBoxLayout, entry: PrioritySearchStats, mode: str) -> None:
        is_mix = mode == "mix"

        cells: List[StatCell] = []

        def add_cell(label: str, value: str, accent: Optional[str] = None) -> None:
            cells.append(StatCell(label, value, accent=accent))

        if not is_mix:
            add_cell("kept", str(entry.kept_count))

        add_cell("matched", str(entry.refined_match_count))
        if entry.has_custom_rules and entry.raw_match_count != entry.refined_match_count:
            add_cell("raw", str(entry.raw_match_count))

        if not is_mix:
            can_be_discarded = entry.limit is not None or self._global_limit_active
            if can_be_discarded:
                discarded_total = entry.limit_discarded + entry.global_limit_discarded
                accent = _accent_red() if discarded_total > 0 else None
                limit_str = f" / limit {entry.limit}" if entry.limit is not None else ""
                add_cell("discarded", f"{discarded_total}{limit_str}", accent=accent)

            if self._cutoff_active:
                add_cell("cutoff-dropped", str(entry.cutoff_dropped))

            if entry.final_start_index is not None:
                add_cell("starts at", str(entry.final_start_index))

        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(0)
        for i, cell in enumerate(cells):
            if i > 0:
                sep = QLabel("·")
                sep.setStyleSheet(f"color: {_muted_color()};")
                sep.setContentsMargins(10, 0, 10, 0)
                stats_row.addWidget(sep)
            stats_row.addWidget(cell)
        stats_row.addStretch(1)
        body_layout.addLayout(stats_row)

        if is_mix:
            note = QLabel("(mix mode — kept/discarded combined in totals)")
            f = note.font()
            f.setItalic(True)
            note.setFont(f)
            note.setStyleSheet(f"color: {_muted_color()};")
            body_layout.addWidget(note)
            return

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        kept_added = self._add_browser_button(btn_row, "Open kept in browser", entry.kept_note_ids)
        disc_added = self._add_browser_button(btn_row, "Open discarded in browser", entry.discarded_note_ids)

        btn_row.addStretch(1)
        if kept_added or disc_added:
            body_layout.addLayout(btn_row)

    def _add_browser_button(self, row: QHBoxLayout, label: str, note_ids: List[int]) -> bool:
        if not note_ids:
            return False
        ids = list(note_ids)
        btn = QPushButton(f"{label} ({len(ids)})")
        qconnect(btn.clicked, lambda _=False, ids=ids: _open_in_browser(ids))
        row.addWidget(btn)
        return True


class StatsDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Priority Reorder Stats")
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
        inner_layout.setSpacing(10)
        inner_layout.setContentsMargins(0, 0, 0, 0)

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
            conf = mw.addonManager.getConfig(pkg)
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

        f = QFont()
        f.setBold(True)
        fm = QFontMetrics(f)
        max_text_w = 0
        for entry in report.entries:
            text = f"▼   [{entry.index + 1}]   {entry.query}"
            w = fm.horizontalAdvance(text)
            if w > max_text_w:
                max_text_w = w

        # Account for: dialog margins (24) + card horizontal padding (20)
        # + card border (2) + scroll-area vertical scrollbar reserve (~24)
        # + a small breathing buffer.
        chrome = 90
        return max(720, max_text_w + chrome)


_dialog: Optional[StatsDialog] = None


def show_stats_window() -> None:
    global _dialog
    if _dialog is None:
        _dialog = StatsDialog(parent=mw)
    else:
        _dialog.refresh()
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
