from typing import List, Optional

from aqt import mw
from aqt.qt import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.theme import theme_manager
from aqt.utils import tooltip

from .reorder_log import PrioritySearchStats, ReorderReport, get_last_report


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


def _copy_to_clipboard(text: str, label: str) -> None:
    QApplication.clipboard().setText(text)
    tooltip(f"Copied {label}")


class StatRow(QWidget):
    """A single label+value pair, label dimmed, value bold."""

    def __init__(self, label: str, value: str, *, accent: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

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
        layout.addStretch(1)


class SearchCard(QFrame):
    """Collapsible card for a single priority search."""

    def __init__(self, entry: PrioritySearchStats, mode: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.entry = entry
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

        # Header: collapse toggle + title
        self._header_btn = QPushButton()
        self._header_btn.setCheckable(True)
        self._header_btn.setChecked(True)
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.setStyleSheet(
            "QPushButton {"
            "  text-align: left;"
            "  padding: 2px 0;"
            "  border: none;"
            "  background: transparent;"
            "  font-weight: bold;"
            "}"
        )
        self._title_text = f"[{entry.index + 1}]   {entry.query}"
        self._update_header_text(True)
        qconnect(self._header_btn.toggled, self._on_collapse_toggle)
        outer.addWidget(self._header_btn)

        # Body
        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        outer.addWidget(self._body)

        self._populate_body(body_layout, entry, mode)

    def _update_header_text(self, expanded: bool) -> None:
        arrow = "▼" if expanded else "▶"
        self._header_btn.setText(f"{arrow}   {self._title_text}")

    def _on_collapse_toggle(self, expanded: bool) -> None:
        self._body.setVisible(expanded)
        self._update_header_text(expanded)

    def _populate_body(self, body_layout: QVBoxLayout, entry: PrioritySearchStats, mode: str) -> None:
        is_mix = mode == "mix"

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(4)

        cells: List[StatRow] = []

        def add_cell(label: str, value: str, accent: Optional[str] = None) -> None:
            cells.append(StatRow(label, value, accent=accent))

        add_cell("matched", str(entry.refined_match_count))
        if entry.has_custom_rules and entry.raw_match_count != entry.refined_match_count:
            add_cell("raw", str(entry.raw_match_count))

        if not is_mix:
            add_cell("kept", str(entry.kept_count))
            discarded_total = entry.limit_discarded + entry.global_limit_discarded
            accent = _accent_red() if discarded_total > 0 else None
            limit_str = f" / limit {entry.limit}" if entry.limit is not None else ""
            add_cell("discarded", f"{discarded_total}{limit_str}", accent=accent)
            add_cell("cutoff-dropped", str(entry.cutoff_dropped))

        for i, cell in enumerate(cells):
            grid.addWidget(cell, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        body_layout.addLayout(grid)

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
        any_btn = False

        if entry.kept_note_ids:
            btn = QPushButton(f"Copy kept nids ({len(entry.kept_note_ids)})")
            qconnect(btn.clicked, lambda: _copy_to_clipboard(
                _nid_search(entry.kept_note_ids), f"{len(entry.kept_note_ids)} kept note IDs"
            ))
            btn_row.addWidget(btn)
            any_btn = True

        if entry.discarded_note_ids:
            btn = QPushButton(f"Copy discarded ({len(entry.discarded_note_ids)})")
            qconnect(btn.clicked, lambda: _copy_to_clipboard(
                _nid_search(entry.discarded_note_ids), f"{len(entry.discarded_note_ids)} discarded note IDs"
            ))
            btn_row.addWidget(btn)
            any_btn = True

        if entry.cutoff_note_ids:
            btn = QPushButton(f"Copy cutoff ({len(entry.cutoff_note_ids)})")
            qconnect(btn.clicked, lambda: _copy_to_clipboard(
                _nid_search(entry.cutoff_note_ids), f"{len(entry.cutoff_note_ids)} cutoff note IDs"
            ))
            btn_row.addWidget(btn)
            any_btn = True

        self._toggle_nids_btn: Optional[QPushButton] = None
        self._nid_view: Optional[QPlainTextEdit] = None
        if entry.kept_note_ids:
            self._toggle_nids_btn = QPushButton("Show note IDs")
            self._toggle_nids_btn.setCheckable(True)
            qconnect(self._toggle_nids_btn.toggled, self._on_nid_toggle)
            btn_row.addWidget(self._toggle_nids_btn)
            any_btn = True

        btn_row.addStretch(1)
        if any_btn:
            body_layout.addLayout(btn_row)

        if entry.kept_note_ids:
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setPlainText("\n".join(str(n) for n in entry.kept_note_ids))
            view.setMaximumHeight(140)
            view.setVisible(False)
            mono = view.font()
            mono.setStyleHint(mono.StyleHint.Monospace)
            mono.setFamily("monospace")
            view.setFont(mono)
            body_layout.addWidget(view)
            self._nid_view = view

    def _on_nid_toggle(self, checked: bool) -> None:
        if self._nid_view is not None:
            self._nid_view.setVisible(checked)
        if self._toggle_nids_btn is not None:
            self._toggle_nids_btn.setText("Hide note IDs" if checked else "Show note IDs")


class StatsDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Priority Reorder Stats")
        self.resize(660, 560)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 12, 12, 12)
        self._root.setSpacing(10)
        self._cards: List[SearchCard] = []
        self.refresh()

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
        # Header
        header = QLabel(f"Last reorder:  {report.timestamp}")
        f = header.font()
        f.setPointSize(f.pointSize() + 1)
        f.setBold(True)
        header.setFont(f)
        self._root.addWidget(header)

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
            for entry in report.entries:
                card = SearchCard(entry, report.mode)
                inner_layout.addWidget(card)
                self._cards.append(card)
            inner_layout.addStretch(1)

        scroll.setWidget(inner)
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
            card._header_btn.setChecked(expanded)


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
