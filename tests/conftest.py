"""Test bootstrap.

Puts the addon dir on sys.path so modules import flat (no Anki package), and
installs lightweight stub `aqt` / `anki` modules so the core files — which do
`from aqt import mw`, `from anki.collection import OpChangesWithCount`,
`from anki.utils import ids2str` at import time — can be imported headless.

These are baseline stubs installed at collection time. Tests that need a live
fake collection (see test_perf.py) override `sys.modules`/`mw.col` via monkeypatch
and restore cleanly afterwards.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _install_anki_stubs() -> None:
    if "aqt" not in sys.modules:
        aqt = types.ModuleType("aqt")
        aqt.mw = types.SimpleNamespace(col=None)
        sys.modules["aqt"] = aqt

    if "anki" not in sys.modules:
        sys.modules["anki"] = types.ModuleType("anki")

    if "anki.collection" not in sys.modules:
        anki_collection = types.ModuleType("anki.collection")

        class OpChangesWithCount:
            def __init__(self, count=0):
                self.count = count

        anki_collection.OpChangesWithCount = OpChangesWithCount
        sys.modules["anki.collection"] = anki_collection

    if "anki.utils" not in sys.modules:
        anki_utils = types.ModuleType("anki.utils")
        anki_utils.ids2str = lambda ids: "(" + ",".join(str(i) for i in ids) + ")"
        sys.modules["anki.utils"] = anki_utils


_install_anki_stubs()
