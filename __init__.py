"""
Priority Reorder Addon - Main entry point.
"""

from aqt import mw
from aqt.utils import showInfo, qconnect
from aqt.qt import QAction, QKeySequence
from aqt import gui_hooks
from aqt.operations import CollectionOp

from .reorderer import run_reorder
from .config_manager import get_config

def run_in_background() -> None:
    """Run the reordering operation in the background"""
    operation = CollectionOp(parent=mw, op=run_reorder).failure(
        lambda err: showInfo(f"Error during reordering: {err}")
    )
    operation.run_in_background()

def setup_sync_hook() -> None:
    """Set up sync hook if enabled in config"""
    if get_config().reorder_on_sync:
        gui_hooks.sync_did_finish.append(lambda: run_reorder(mw.col))

def setup_menu() -> None:
    """Set up menu entries and shortcuts"""
    action = QAction("Reorder Cards", mw)
    action.setShortcut(QKeySequence("Ctrl+Alt+`"))
    qconnect(action.triggered, run_in_background)
    mw.form.menuTools.addAction(action)

# Initialize the addon
setup_sync_hook()
setup_menu()