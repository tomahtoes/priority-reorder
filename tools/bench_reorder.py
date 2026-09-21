"""Replay a real reorder against a real Anki collection, and time where it goes.

The unit tests pin structure and call counts, never wall clock, which is what keeps them
honest but also means nothing in the suite would notice a reorder getting ten times slower.
This is the other half: it reads the installed addon's own `meta.json` for the config and the
profile's `collection.anki2` for the cards, so the numbers describe the setup that is actually
slow rather than a synthetic one.

Read-only, and enforced at the sqlite layer (`mode=ro&immutable=1`), so it is safe to point at
a live profile. Anki does not need to be closed. Nothing is repositioned: the benchmark stops
after the searches, which is where the time goes.

`mw` is stubbed rather than imported, so this runs under plain CPython with no Anki present.
The collection is opened through the stub's `db`, which means every query the addon issues is
timed and counted, and `--sql` prints them slowest-first, which is how the 7-second join in
_bulk_load was found.

Two runs matter and they are not the same. Run 1 is the first reorder of an Anki session, and
pays for parsing the occurrence dictionaries, merging the seen windows and scanning the
learned collection. Later runs pay none of that and show the per-reorder floor. `col.mod` is
bumped between runs, because a real reorder repositions cards, which is what invalidates
search.py's resolution memos.

Usage:
    python tools/bench_reorder.py                       # default profile, 3 runs
    python tools/bench_reorder.py --profile Ryan
    python tools/bench_reorder.py --runs 5 --sql
    python tools/bench_reorder.py --collection path/to/collection.anki2 --addon path/to/addon
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import types
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def anki_base() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", ""), "Anki2")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Anki2")
    return os.path.expanduser("~/.local/share/Anki2")


def find_profile(base: str, wanted=None) -> str:
    """The profile directory to read. Anki's own bookkeeping folders are not profiles."""
    if wanted:
        return os.path.join(base, wanted)
    skip = {"addons21", "crash.log", "prefs21.db", "prefs21.db-journal"}
    candidates = [
        name for name in sorted(os.listdir(base))
        if name not in skip and os.path.isdir(os.path.join(base, name))
        and os.path.exists(os.path.join(base, name, "collection.anki2"))
    ]
    if not candidates:
        raise SystemExit("no Anki profile with a collection found under %s" % base)
    return os.path.join(base, candidates[0])


class TimedDB:
    """The addon's `mw.col.db` surface, over a read-only sqlite handle, timing every query."""

    def __init__(self, path: str):
        uri = "file:///" + urllib.parse.quote(path.replace("\\", "/")) + "?mode=ro&immutable=1"
        self.conn = sqlite3.connect(uri, uri=True)
        # Anki registers this collation for its own text indexes; a plain sqlite handle has
        # to supply one or any query touching them fails to prepare.
        self.conn.create_collation(
            "unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower())
        )
        self.log = []
        self.ms = 0.0

    def _run(self, sql, *args):
        t0 = time.perf_counter()
        rows = self.conn.execute(sql, args).fetchall()
        elapsed = (time.perf_counter() - t0) * 1000
        self.ms += elapsed
        self.log.append((elapsed, len(rows), re.sub(r"\s+", " ", sql)))
        return rows

    def all(self, sql, *args):
        return [tuple(r) for r in self._run(sql, *args)]

    def list(self, sql, *args):
        return [r[0] for r in self._run(sql, *args)]

    def first(self, sql, *args):
        rows = self._run(sql, *args)
        return tuple(rows[0]) if rows else None

    def scalar(self, sql, *args):
        rows = self._run(sql, *args)
        return rows[0][0] if rows else None

    def execute(self, sql, *args):
        return self._run(sql, *args)

    def reset(self):
        self.log = []
        self.ms = 0.0


class Models:
    """Note types read from the collection's own `fields` table."""

    def __init__(self, db: TimedDB):
        self.by_id = {}
        for ntid, ord_, name in db.conn.execute("select ntid, ord, name from fields"):
            self.by_id.setdefault(ntid, {})[name] = ord_

    def all(self):
        return [{"id": ntid} for ntid in self.by_id]

    def get(self, mid):
        return {"id": mid} if mid in self.by_id else None

    def field_map(self, model):
        return {n: (o, {"name": n}) for n, o in self.by_id.get(model["id"], {}).items()}


class Collection:
    def __init__(self, path: str):
        self.db = TimedDB(path)
        self.models = Models(self.db)
        self.mod = 1
        self.find_calls = 0
        self._new_by_deck = {}

    # decks.name joins hierarchy levels with U+001F; a search writes "::" instead.
    _SEPARATOR = chr(0x1F)

    def _deck_ids(self, name_fragment: str):
        return [did for did, name in self.db.conn.execute("select id, name from decks")
                if name_fragment in name.replace(self._SEPARATOR, "::")]

    def find_cards(self, query: str):
        """Enough of Anki's search to serve the addon's own queries.

        Only `deck:` and `is:new` are interpreted, plus the `nid:` clauses the addon
        substitutes; anything else widens the result rather than narrowing it. That makes the
        candidate set a superset of the real one, so the timings are if anything pessimistic,
        and it avoids reimplementing a search engine to measure one.
        """
        self.find_calls += 1
        deck = re.search(r'deck:("[^"]*"|[^\s()]+)', query)
        key = deck.group(1) if deck else None
        if key not in self._new_by_deck:
            if key:
                dids = self._deck_ids(key.strip('"'))
                placeholders = ",".join(str(d) for d in dids) or "0"
                sql = "select id from cards where type = 0 and did in (%s)" % placeholders
            else:
                sql = "select id from cards where type = 0"
            self._new_by_deck[key] = [r[0] for r in self.db.conn.execute(sql)]
        cards = self._new_by_deck[key]
        nid_clauses = re.findall(r"nid:([0-9,]+)", query)
        if not nid_clauses:
            return list(cards)
        allowed = set.intersection(*[{int(x) for x in c.split(",")} for c in nid_clauses])
        rows = self.db.conn.execute("select id, nid from cards where type = 0")
        return [cid for cid, nid in rows if nid in allowed and cid in set(cards)]

    def find_notes(self, query: str):
        cards = self.find_cards(query)
        if not cards:
            return []
        rows = self.db.conn.execute("select id, nid from cards where type = 0")
        wanted = set(cards)
        return [nid for cid, nid in rows if cid in wanted]


def install_stubs(collection_path: str):
    aqt = types.ModuleType("aqt")
    aqt.mw = types.SimpleNamespace(col=Collection(collection_path))
    sys.modules["aqt"] = aqt
    sys.modules["anki"] = types.ModuleType("anki")

    utils = types.ModuleType("anki.utils")
    utils.ids2str = lambda ids: "(" + ",".join(str(i) for i in ids) + ")"
    sys.modules["anki.utils"] = utils

    collection = types.ModuleType("anki.collection")

    class OpChangesWithCount:
        def __init__(self, count=0):
            self.count = count

    collection.OpChangesWithCount = OpChangesWithCount
    sys.modules["anki.collection"] = collection
    return aqt.mw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", help="Anki profile name (default: the first one found)")
    parser.add_argument("--collection", help="path to collection.anki2")
    parser.add_argument("--addon", help="addon dir holding meta.json (default: the installed copy)")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--sql", action="store_true", help="also print each run's queries, slowest first")
    args = parser.parse_args()

    base = anki_base()
    collection_path = args.collection or os.path.join(find_profile(base, args.profile),
                                                      "collection.anki2")
    addon_dir = args.addon or os.path.join(base, "addons21", "priority-reorder")
    meta_path = os.path.join(addon_dir, "meta.json")
    if not os.path.exists(meta_path):
        raise SystemExit("no meta.json at %s; pass --addon" % meta_path)
    if not os.path.exists(collection_path):
        raise SystemExit("no collection at %s; pass --collection" % collection_path)

    config_data = json.load(open(meta_path, encoding="utf-8")).get("config", {})
    mw = install_stubs(collection_path)

    # The working tree is timed; only the config and the user_files come from the install.
    sys.path.insert(0, ROOT)
    import config_manager
    import data_manager
    import dictionary_manager

    config = config_manager.Config.from_dict(config_data)
    config_manager.get_config = lambda: config

    if os.path.abspath(addon_dir) != os.path.abspath(ROOT):
        # Dictionaries and seen data live beside the installed copy, not in the repo.
        live_user_files = os.path.join(addon_dir, "user_files")
        dictionary_manager._dict_dir = lambda name: os.path.join(live_user_files, name)
        dictionary_manager.get_all_dict_names = lambda: sorted(
            item for item in os.listdir(live_user_files)
            if item not in ("all", dictionary_manager.SEEN_FOLDER)
            and not item.startswith(".")
            and os.path.isdir(os.path.join(live_user_files, item))
        )

    searches = config.priority_search
    searches = [searches] if isinstance(searches, str) else list(searches or [])
    print("collection : %s" % collection_path)
    print("addon      : %s" % addon_dir)
    print("code       : %s" % ROOT)
    print("searches   : %d priority + 1 normal\n" % len(searches))

    for run in range(1, args.runs + 1):
        mw.col.mod += 1  # a real reorder repositions cards, which invalidates the nid memos
        mw.col.db.reset()
        manager = data_manager.DataManager(config)
        t0 = time.perf_counter()
        matched = 0
        for query in searches:
            matched += len(manager.get_cards_from_search(query).cards)
        matched += len(manager.get_cards_from_search(config.normal_search).cards)
        total = (time.perf_counter() - t0) * 1000

        label = "run %d (cold)" % run if run == 1 else "run %d (hot)" % run
        print("%-14s %8.0f ms   sql %7.0f ms / %2d queries   matches %d"
              % (label, total, mw.col.db.ms, len(mw.col.db.log), matched))
        for key, value in sorted(manager.stage_ms.items(), key=lambda kv: -kv[1]):
            print("                 %-14s %8.1f ms" % (key, value))
        if args.sql:
            for elapsed, rows, sql in sorted(mw.col.db.log, reverse=True)[:8]:
                print("                 %8.1f ms  rows=%-7d %s" % (elapsed, rows, sql[:96]))
        print()


if __name__ == "__main__":
    main()
