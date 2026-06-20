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

    # True while Anki is tearing down the profile (profile_will_close ..
    # profile_did_open). During this window the collection is about to be
    # unloaded, so the async CollectionOp path below would race col.close()
    # and read a None mw.col mid-flight; close-time reorders run synchronously
    # instead (see handle_sync_did_finish / run_reorder_on_close).
    _is_closing = False

    # Prevents infinite sync loop
    _auto_syncing = False

    def trigger_second_sync() -> None:
        """Trigger a second sync if reordering was triggered by a sync."""
        global _auto_syncing
        _auto_syncing = True
        mw.onSync()

    def run_in_background(manual: bool = True) -> None:
        """Run the reordering operation in the background"""
        if mw.col is None:
            return
        operation = CollectionOp(parent=mw, op=run_reorder).failure(
            lambda err: showInfo(f"Error during reordering: {err}")
        )

        if not manual:
            operation.success(lambda _: trigger_second_sync())

        operation.run_in_background()

    def run_reorder_on_close() -> None:
        """Reorder synchronously during shutdown.

        sync_did_finish fires on the main thread with mw.col still alive, just
        before Anki unloads the collection. Running here (rather than scheduling
        a background CollectionOp) completes before col.close(), so the reorder
        is applied and persisted without racing teardown. Errors are only logged
        — never shown as a dialog as the app is exiting."""
        if mw.col is None:
            return
        try:
            run_reorder()
        except Exception as e:
            print(f"[priority-reorder] Reorder on close failed: {e}")

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

                    # Chain the reorder process on sync completion if not manual.
                    # Pass manual=False so the reorder triggers a follow-up sync to
                    # push the new positions (run_on_main would otherwise call
                    # run_in_background with no args, defaulting manual to True).
                    if reorder_after:
                        mw.taskman.run_on_main(lambda: run_in_background(manual=False))
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
        global _auto_syncing

        if _auto_syncing:
            _auto_syncing = False
            return

        config = get_config()

        if _is_closing:
            # Closing: skip the networked dict-updater (it can't finish during
            # shutdown) and run the reorder synchronously before col is unloaded.
            if config.reorder_on_sync:
                run_reorder_on_close()
            return

        if config.auto_update_dicts:
            run_updater_background(manual=False)
        elif config.reorder_on_sync:
            run_in_background(manual=False)

    def _on_profile_will_close() -> None:
        global _is_closing
        _is_closing = True

    def _on_profile_did_open() -> None:
        # Fires before the startup auto-sync, so startup/manual syncs take the
        # normal async path.
        global _is_closing
        _is_closing = False

    def setup_sync_hook() -> None:
        """Set up sync hook if enabled in config"""
        gui_hooks.sync_did_finish.append(handle_sync_did_finish)
        gui_hooks.profile_will_close.append(_on_profile_will_close)

    def setup_search_terms() -> None:
        """Register the custom search terms (occurrences:/f/kanji:) so they work in the
        Browse bar and via the collection API. Installed once a profile is loaded, since
        patching Collection methods needs a live collection."""
        gui_hooks.profile_did_open.append(search.install)
        # A freshly opened profile must not show (or open in the browser) the
        # previous profile's reorder report.
        gui_hooks.profile_did_open.append(clear_last_report)
        # A new profile means we're no longer in a close sequence.
        gui_hooks.profile_did_open.append(_on_profile_did_open)

    def setup_menu() -> None:
        """Set up menu entries and shortcuts"""
        from aqt.qt import QMenu
        menu = QMenu("Priority Reorder", mw)

        reorder_action = QAction("Reorder Cards", mw)
        reorder_action.setShortcut(QKeySequence("Ctrl+Alt+`"))
        # Wrap in a lambda so Qt's triggered(checked) bool isn't passed as the
        # `manual` arg — a menu/shortcut reorder must stay manual (no auto sync).
        qconnect(reorder_action.triggered, lambda: run_in_background())
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
