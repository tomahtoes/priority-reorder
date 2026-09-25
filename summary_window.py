from typing import List, Optional, Set

from aqt import dialogs, gui_hooks, mw  # type: ignore
from aqt.operations import CollectionOp  # type: ignore
from aqt.qt import QDialog, QFont, QFontMetrics, Qt, QVBoxLayout, QWidget  # type: ignore
from aqt.theme import theme_manager  # type: ignore
from aqt.utils import showInfo  # type: ignore
from aqt.webview import AnkiWebView  # type: ignore

from .config_manager import migrate_config_in_place
from .reorder_log import PrioritySearchSummary, get_last_report, get_previous_report
from .reorderer import run_reorder
from .summary_html import render_summary, shared_prefix, strip_prefix


def _is_dark() -> bool:
    try:
        return bool(theme_manager.night_mode)
    except Exception:
        return False


def _ui_font_px() -> Optional[float]:
    # Qt point sizes are at 72 per inch and CSS pixels at 96, so 9pt comes out as 12px.
    pt = QFont().pointSizeF()
    return pt * 96 / 72 if pt > 0 else None


def _open_in_browser(note_ids: List[int]) -> None:
    if not note_ids:
        return
    browser = dialogs.open("Browser", mw)
    browser.search_for("nid:" + ",".join(str(n) for n in note_ids))


# Window width parts, in pixels at the Qt UI font. _COLUMNS_WIDTH covers the caret, #,
# Kept, Matched and Queue columns and every cell's padding; _MIN_WIDTH fits the header
# row (counts, run time, two buttons).
_COLUMNS_WIDTH = 300
_LEEWAY = 60
_MIN_WIDTH = 560


class SummaryDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Priority Reorder Summary")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # ConfigEditor expects parent.mgr to be the addon manager.
        self.mgr = mw.addonManager
        # Expanded rows, by search index, so a re-render (theme change, Run reorder)
        # keeps what the user had open.
        self._open_rows: Set[int] = set()
        self._reordering = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web = AnkiWebView(parent=self, title="priority reorder summary")
        self.web.set_bridge_command(self._on_bridge_cmd, self)
        layout.addWidget(self.web)

        # Query colors are baked into the markup per theme, so a theme switch needs a
        # fresh render rather than the webview's own night-mode class flip.
        gui_hooks.theme_did_change.append(self.refresh)
        self.refresh()
        self._size_to_screen()

    def refresh(self) -> None:
        body = render_summary(
            get_last_report(),
            get_previous_report(),
            dark=_is_dark(),
            open_rows=self._open_rows,
            reordering=self._reordering,
            font_px=_ui_font_px(),
        )
        self.web.stdHtml(body, js=[], context=self)

    def reject(self) -> None:
        # Reachable twice (Escape inside the page sends "close", and the title-bar
        # close also lands here), so the teardown is guarded.
        global _dialog
        if _dialog is self:
            gui_hooks.theme_did_change.remove(self.refresh)
            self.web.cleanup()
            _dialog = None
        super().reject()

    def _size_to_screen(self) -> None:
        # The longest search as displayed (shared prefix removed, same font as the page)
        # plus the other columns and some leeway, so that search sits on one line. Floored
        # at what the header row needs, capped at most of the screen; past that, queries wrap.
        screen = self.screen()
        avail = screen.availableGeometry() if screen is not None else None
        width = _MIN_WIDTH
        report = get_last_report()
        if report is not None and report.entries:
            queries = [e.query for e in report.entries]
            prefix = shared_prefix(queries)
            fm = QFontMetrics(QFont())
            longest = max(fm.horizontalAdvance(strip_prefix(q, prefix)) for q in queries)
            width = max(_MIN_WIDTH, longest + _COLUMNS_WIDTH + _LEEWAY)
        if avail is not None:
            width = min(width, int(avail.width() * 0.85))
        height = int(avail.height() * 0.8) if avail is not None else 720
        self.resize(width, height)

    def _entry(self, index: str) -> Optional[PrioritySearchSummary]:
        report = get_last_report()
        try:
            i = int(index)
        except ValueError:
            return None
        if report is None or not 0 <= i < len(report.entries):
            return None
        return report.entries[i]

    def _on_bridge_cmd(self, cmd: str) -> None:
        if cmd == "close":
            self.reject()
        elif cmd == "config":
            self._open_addon_config()
        elif cmd == "reorder":
            self._run_reorder_now()
        elif cmd.startswith("toggle:"):
            try:
                i = int(cmd.split(":", 1)[1])
            except ValueError:
                return
            self._open_rows.symmetric_difference_update({i})
        elif cmd.startswith("browse:"):
            _, kind, index = (cmd.split(":", 2) + ["", ""])[:3]
            entry = self._entry(index)
            if entry is None:
                return
            ids = {
                "kept": entry.kept_note_ids,
                "discarded": entry.discarded_note_ids,
                "cutoff": entry.cutoff_note_ids,
            }.get(kind, [])
            _open_in_browser(list(ids))

    def _open_addon_config(self) -> None:
        from aqt.addons import ConfigEditor  # type: ignore

        pkg = __name__.split(".")[0]
        try:
            # Normally a no-op, since the config was migrated at addon load. It matters
            # only if that write failed (read-only meta.json), where reads still
            # work but the editor would otherwise show the pre-section layout.
            conf = migrate_config_in_place(pkg)
            ConfigEditor(self, pkg, conf)
        except Exception as e:
            showInfo(f"Could not open config: {e}")

    def _run_reorder_now(self) -> None:
        if self._reordering:
            return
        self._reordering = True

        def on_success(_changes) -> None:
            self._reordering = False
            if _dialog is self:
                self.refresh()

        def on_failure(err) -> None:
            self._reordering = False
            if _dialog is self:
                self.refresh()
            showInfo(f"Error during reordering: {err}")

        op = CollectionOp(parent=self, op=run_reorder).success(on_success).failure(on_failure)
        op.run_in_background()


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
