"""
Priority Reorder Addon - Main entry point.
"""

try:
    from aqt import mw
    from aqt.utils import showInfo, tooltip, qconnect
    from aqt.qt import QAction, QKeySequence
    from aqt import gui_hooks
    from aqt.operations import CollectionOp, QueryOp
except ImportError:
    # Not running inside a full Anki (e.g. pytest, whose conftest stubs only the
    # bits of `aqt`/`anki` the individual modules import). The unit tests import
    # those modules directly, so the GUI entry point here is a no-op.
    pass
else:
    import threading

    from .reorderer import run_reorder
    from .config_manager import get_config
    from .stats_window import show_stats_window
    from .reorder_log import clear_last_report
    from . import search

    from .updater import JitenUpdater

    def run_in_background() -> None:
        """Run the reordering operation in the background"""
        operation = CollectionOp(parent=mw, op=run_reorder).failure(
            lambda err: showInfo(f"Error during reordering: {err}")
        )
        operation.run_in_background()

    def show_updater_results(results: tuple[int, int]) -> None:
        updated_count, failed_count = results

        if updated_count > 0:
            msg = f"Updated {updated_count} dictionaries."
            if failed_count > 0:
                msg += f" (Failed: {failed_count})"
            tooltip(msg)
        elif failed_count > 0:
            tooltip(f"Failed to update {failed_count} dictionaries.")
        else:
            tooltip("All dictionaries are already up to date.")

    # Serializes updater runs: every sync (and the menu entry) may spawn one, and
    # two workers racing on the same dictionary directories must never overlap.
    _updater_lock = threading.Lock()

    def run_updater_background(manual: bool) -> None:
        """Runs the updater purely in the background via threading to avoid Anki's locking task manager."""
        if not _updater_lock.acquire(blocking=False):
            tooltip("Dictionary update already in progress.")
            return

        try:
            updater = JitenUpdater()
            total_dicts = updater.get_dictionary_count()

            try:
                next_day_cutoff = getattr(mw.col.sched, "day_cutoff", getattr(mw.col.sched, "dayCutoff", 0))
            except Exception:
                import time
                next_day_cutoff = int(time.time()) + 86400

            # Snapshot on the main thread; the worker must not read addon config.
            reorder_after = not manual and get_config().reorder_on_sync

            def worker() -> None:
                def show_checking() -> None:
                    mw.taskman.run_on_main(lambda: tooltip(f"Checking updates for {total_dicts} dictionaries..."))

                timer = threading.Timer(0.3, show_checking)
                if total_dicts > 0:
                    timer.start()

                try:
                    results = updater.update_dictionaries(manual=manual, next_day_cutoff=next_day_cutoff)
                    timer.cancel()
                    mw.taskman.run_on_main(lambda: show_updater_results(results))

                    # Chain the reorder process on sync completion if not manual
                    if reorder_after:
                        mw.taskman.run_on_main(run_in_background)
                except Exception as e:
                    timer.cancel()
                    mw.taskman.run_on_main(lambda: showInfo(f"Error during dictionary update: {e}"))
                finally:
                    _updater_lock.release()

            threading.Thread(target=worker, daemon=True).start()
        except BaseException:
            # Worker never started; it can't release the lock for us.
            _updater_lock.release()
            raise

    def update_jiten_dicts() -> None:
        """Manually triggered update"""
        run_updater_background(manual=True)

    def handle_sync_did_finish() -> None:
        """Run on sync finish to update dicts and run reorder"""
        config = get_config()

        if config.auto_update_dicts:
            run_updater_background(manual=False)
        elif config.reorder_on_sync:
            run_in_background()

    def setup_sync_hook() -> None:
        """Set up sync hook if enabled in config"""
        gui_hooks.sync_did_finish.append(handle_sync_did_finish)

    def setup_search_terms() -> None:
        """Register the custom search terms (occurrences:/f/kanji:) so they work in the
        Browse bar and via the collection API. Installed once a profile is loaded, since
        patching Collection methods needs a live collection."""
        gui_hooks.profile_did_open.append(search.install)
        # A freshly opened profile must not show (or open in the browser) the
        # previous profile's reorder report.
        gui_hooks.profile_did_open.append(clear_last_report)

    def setup_menu() -> None:
        """Set up menu entries and shortcuts"""
        from aqt.qt import QMenu
        menu = QMenu("Priority Reorder", mw)

        reorder_action = QAction("Reorder Cards", mw)
        reorder_action.setShortcut(QKeySequence("Ctrl+Alt+`"))
        qconnect(reorder_action.triggered, run_in_background)
        menu.addAction(reorder_action)

        stats_action = QAction("Show Stats", mw)
        qconnect(stats_action.triggered, show_stats_window)
        menu.addAction(stats_action)

        update_dicts_action = QAction("Update Jiten Occurrence Dictionaries", mw)
        qconnect(update_dicts_action.triggered, update_jiten_dicts)
        menu.addAction(update_dicts_action)

        mw.form.menuTools.addMenu(menu)

    # Initialize the addon
    setup_sync_hook()
    setup_search_terms()
    setup_menu()
