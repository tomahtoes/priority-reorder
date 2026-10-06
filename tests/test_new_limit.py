"""today_new_limit: settling a deck's Today-only new card limit (new_limit.py).

The fake collection mirrors the legacy deck dicts and counters Anki 26.9 exposes. Checked
against a real collection: studying a card bumps `newToday` on its deck and every parent,
and a Today-only limit counts the cards already studied that day."""

import copy
import re
import types

from config_manager import Config
from new_limit import MARKER_KEY, apply_today_limits, merge_op_changes

TODAY = 100


class _OpChanges:
    def __init__(self, **flags):
        self.flags = flags

    def ListFields(self):
        return [(types.SimpleNamespace(name=k), v) for k, v in self.flags.items()]


class _Decks:
    def __init__(self, col):
        self.col = col
        self.decks = {}
        self.per_day = {}
        self.writes = []

    def add(self, did, name, per_day=10, new_limit=None, studied=0,
            limit_today=None, dyn=False):
        self.decks[did] = {
            "id": did, "name": name, "dyn": 1 if dyn else 0,
            "newLimit": new_limit,
            "newToday": [TODAY, studied] if studied else [TODAY - 1, 4],
            "newLimitToday": limit_today,
        }
        self.per_day[did] = per_day

    def id_for_name(self, name):
        return next((d for d, v in self.decks.items() if v["name"] == name), None)

    def get(self, did, default=True):
        deck = self.decks.get(did)
        return copy.deepcopy(deck) if deck else None

    def name(self, did):
        return self.decks[did]["name"]

    def deck_and_child_ids(self, did):
        root = self.decks[did]["name"]
        return [d for d, v in self.decks.items()
                if v["name"] == root or v["name"].startswith(root + "::")]

    def config_dict_for_deck_id(self, did):
        return {"new": {"perDay": self.per_day[did]}}

    def update_dict(self, deck):
        self.writes.append((deck["name"], deck["newLimitToday"]))
        self.decks[deck["id"]] = copy.deepcopy(deck)
        return _OpChanges(deck=True, study_queues=True)


class _DB:
    def __init__(self):
        self.cards = []  # (cid, did, queue)
        self.queries = []

    def all(self, query):
        self.queries.append(query)
        dids = {int(x) for x in re.search(r"did in \(([^)]*)\)", query).group(1).split(",")}
        return [(cid, did) for cid, did, queue in self.cards if queue == 0 and did in dids]


class _Col:
    def __init__(self):
        self.sched = types.SimpleNamespace(today=TODAY)
        self.decks = _Decks(self)
        self.db = _DB()
        self.config = {}
        self.config_writes = 0

    def get_config(self, key, default=None):
        return copy.deepcopy(self.config.get(key, default))

    def set_config(self, key, value):
        self.config[key] = copy.deepcopy(value)
        self.config_writes += 1

    def add_cards(self, did, n, start, queue=0):
        """Cards start..start+n-1; ids already present are kept, so a later call can
        grow an earlier queue."""
        ids = list(range(start, start + n))
        have = {c[0] for c in self.db.cards}
        self.db.cards.extend((cid, did, queue) for cid in ids if cid not in have)
        return ids


def cfg(decks=("日本語",), enabled=True, max=None):
    return Config(today_limit_enabled=enabled, today_limit_decks=list(decks),
                  today_limit_max=max)


def my_setup():
    """The layout this feature was designed against: the clicked parent on a 25/day
    preset, and a child whose own "This deck" limit of 9999 never caps it."""
    col = _Col()
    col.decks.add(1, "日本語", per_day=25)
    col.decks.add(2, "日本語::Mining", per_day=25, new_limit=9999)
    return col


def statuses(report):
    return {c.deck: (c.status, c.target) for c in report}


def today_limit(col, did):
    return col.decks.decks[did]["newLimitToday"]


# off / misconfigured

def test_disabled_touches_nothing():
    col = my_setup()
    report, ops = apply_today_limits(col, cfg(enabled=False), col.add_cards(2, 30, 1000))
    assert (report, ops) == ([], [])
    assert col.db.queries == [] and col.decks.writes == [] and col.config_writes == 0


def test_enabled_without_decks_writes_nothing():
    col = my_setup()
    report, ops = apply_today_limits(col, cfg(decks=()), col.add_cards(2, 30, 1000))
    assert (report, ops) == ([], [])
    assert col.decks.writes == []


def test_missing_and_filtered_decks_are_reported():
    col = my_setup()
    col.decks.add(9, "Filtered", dyn=True)
    report, _ = apply_today_limits(col, cfg(decks=("Nope", "Filtered")), [])
    assert statuses(report) == {"Nope": ("missing", 0), "Filtered": ("missing", 0)}
    assert col.decks.writes == []


# raising

def test_raises_the_clicked_deck_and_leaves_an_uncapped_child_alone():
    col = my_setup()
    report, ops = apply_today_limits(col, cfg(), col.add_cards(2, 31, 1000))
    assert statuses(report) == {"日本語": ("raised", 31)}
    assert col.decks.writes == [("日本語", {"limit": 31, "today": TODAY})]
    assert len(ops) == 1
    assert col.config[MARKER_KEY] == {"1": [TODAY, 31]}


def test_queue_that_fits_writes_nothing():
    col = my_setup()
    report, _ = apply_today_limits(col, cfg(), col.add_cards(2, 25, 1000))
    assert statuses(report) == {"日本語": ("not_needed", 25)}
    assert col.decks.writes == [] and col.config_writes == 0


def test_cards_studied_today_are_added_back():
    col = _Col()
    col.decks.add(1, "日本語", per_day=25, studied=5)
    col.decks.add(2, "日本語::Mining", new_limit=9999, studied=5)
    report, _ = apply_today_limits(col, cfg(), col.add_cards(2, 21, 1000))
    assert report[0].target == 26 and report[0].studied == 5


def test_only_cards_in_the_priority_queue_count():
    col = my_setup()
    queue = col.add_cards(2, 30, 1000)
    col.add_cards(2, 500, 5000)  # new cards outside the priority queue
    report, _ = apply_today_limits(col, cfg(), queue)
    assert report[0].target == 30


def test_suspended_and_buried_cards_do_not_count():
    col = my_setup()
    queue = col.add_cards(2, 26, 1000) + col.add_cards(2, 10, 2000, queue=-1) \
        + col.add_cards(2, 10, 3000, queue=-3)
    report, _ = apply_today_limits(col, cfg(), queue)
    assert report[0].target == 26


def test_cards_outside_the_deck_tree_do_not_count():
    col = my_setup()
    col.decks.add(3, "漢字", per_day=4)
    queue = col.add_cards(2, 26, 1000) + col.add_cards(3, 50, 2000)
    report, _ = apply_today_limits(col, cfg(), queue)
    assert statuses(report) == {"日本語": ("raised", 26)}


def test_max_clamps_the_target():
    col = my_setup()
    report, _ = apply_today_limits(col, cfg(max=40), col.add_cards(2, 300, 1000))
    assert statuses(report) == {"日本語": ("raised", 40)}


def test_max_below_baseline_never_lowers():
    col = my_setup()
    report, _ = apply_today_limits(col, cfg(max=10), col.add_cards(2, 300, 1000))
    assert statuses(report) == {"日本語": ("not_needed", 25)}
    assert col.decks.writes == []


def test_this_deck_limit_is_the_baseline():
    col = _Col()
    col.decks.add(1, "Deck", per_day=10, new_limit=50)
    report, _ = apply_today_limits(col, cfg(decks=("Deck",)), col.add_cards(1, 40, 1000))
    assert statuses(report) == {"Deck": ("not_needed", 50)}


def test_a_child_with_a_low_limit_of_its_own_is_raised_too():
    col = _Col()
    col.decks.add(1, "P", per_day=10)
    col.decks.add(2, "P::A", per_day=10)
    col.decks.add(3, "P::B", per_day=10)
    queue = col.add_cards(2, 20, 1000) + col.add_cards(3, 6, 2000)
    report, _ = apply_today_limits(col, cfg(decks=("P",)), queue)
    # B's 6 fit under its own 10, so it isn't reported or written.
    assert statuses(report) == {"P": ("raised", 26), "P::A": ("raised", 20)}
    assert [w[0] for w in col.decks.writes] == ["P", "P::A"]


# settling over the day

def test_before_studying_a_later_reorder_follows_the_queue():
    col = my_setup()
    apply_today_limits(col, cfg(), col.add_cards(2, 30, 1000))
    report, _ = apply_today_limits(col, cfg(), col.add_cards(2, 36, 1000))
    assert statuses(report) == {"日本語": ("raised", 36)}
    assert today_limit(col, 1) == {"limit": 36, "today": TODAY}


def test_an_unchanged_queue_rewrites_nothing():
    col = my_setup()
    queue = col.add_cards(2, 30, 1000)
    apply_today_limits(col, cfg(), queue)
    writes, config_writes = len(col.decks.writes), col.config_writes
    report, ops = apply_today_limits(col, cfg(), queue)
    assert statuses(report) == {"日本語": ("raised", 30)}
    assert ops == [] and len(col.decks.writes) == writes and col.config_writes == config_writes


def test_before_studying_a_shrunk_queue_clears_the_limit():
    col = my_setup()
    apply_today_limits(col, cfg(), col.add_cards(2, 30, 1000))
    report, _ = apply_today_limits(col, cfg(), list(range(1000, 1010)))
    assert statuses(report) == {"日本語": ("cleared", 25)}
    assert today_limit(col, 1) is None
    assert col.config[MARKER_KEY] == {}


def test_once_studying_starts_the_limit_is_frozen():
    col = my_setup()
    apply_today_limits(col, cfg(), col.add_cards(2, 30, 1000))
    col.decks.decks[1]["newToday"] = [TODAY, 3]
    report, ops = apply_today_limits(col, cfg(), col.add_cards(2, 60, 1000))
    assert statuses(report) == {"日本語": ("frozen", 30)}
    assert ops == [] and today_limit(col, 1) == {"limit": 30, "today": TODAY}


def test_first_write_after_studying_began_lands_once():
    col = _Col()
    col.decks.add(1, "日本語", per_day=25, studied=5)
    col.decks.add(2, "日本語::Mining", new_limit=9999, studied=5)
    report, _ = apply_today_limits(col, cfg(), col.add_cards(2, 21, 1000))
    assert statuses(report) == {"日本語": ("raised", 26)}
    report, _ = apply_today_limits(col, cfg(), col.add_cards(2, 40, 1000))
    assert statuses(report) == {"日本語": ("frozen", 26)}


def test_a_hand_set_limit_is_left_alone():
    col = _Col()
    col.decks.add(1, "日本語", per_day=25, limit_today={"limit": 15, "today": TODAY})
    report, ops = apply_today_limits(col, cfg(), col.add_cards(1, 40, 1000))
    assert statuses(report) == {"日本語": ("hand_set", 15)}
    assert ops == []


def test_a_limit_edited_after_the_addon_wrote_it_counts_as_hand_set():
    col = my_setup()
    queue = col.add_cards(2, 30, 1000)
    apply_today_limits(col, cfg(), queue)
    col.decks.decks[1]["newLimitToday"] = {"limit": 50, "today": TODAY}
    report, _ = apply_today_limits(col, cfg(), queue)
    assert statuses(report) == {"日本語": ("hand_set", 50)}
    assert col.config[MARKER_KEY] == {}
    # Ownership doesn't come back, even once the queue matches the user's value again.
    report, _ = apply_today_limits(col, cfg(), col.add_cards(2, 50, 1000))
    assert statuses(report) == {"日本語": ("hand_set", 50)}


def test_a_stale_limit_from_yesterday_is_replaced():
    col = _Col()
    col.decks.add(1, "日本語", per_day=25, limit_today={"limit": 80, "today": TODAY - 1})
    col.config[MARKER_KEY] = {"1": [TODAY - 1, 80], "7": [TODAY - 3, 12]}
    report, _ = apply_today_limits(col, cfg(), col.add_cards(1, 30, 1000))
    assert statuses(report) == {"日本語": ("raised", 30)}
    assert col.config[MARKER_KEY] == {"1": [TODAY, 30]}


def test_a_subdeck_raised_earlier_is_cleared_when_it_empties():
    col = _Col()
    col.decks.add(1, "P", per_day=10)
    col.decks.add(2, "P::A", per_day=10)
    apply_today_limits(col, cfg(decks=("P",)), col.add_cards(2, 20, 1000))
    report, _ = apply_today_limits(col, cfg(decks=("P",)), [])
    assert statuses(report) == {"P": ("cleared", 10), "P::A": ("cleared", 10)}
    assert today_limit(col, 2) is None


def test_naming_a_deck_and_its_parent_settles_each_once():
    col = _Col()
    col.decks.add(1, "P", per_day=10)
    col.decks.add(2, "P::A", per_day=10)
    report, _ = apply_today_limits(col, cfg(decks=("P", "P::A", "P")), col.add_cards(2, 20, 1000))
    assert [c.deck for c in report] == ["P", "P::A"]
    assert len(col.decks.writes) == 2


# merging into the reorder's result

def test_merge_op_changes_ors_flags_into_the_result():
    result = types.SimpleNamespace(changes=types.SimpleNamespace(card=True, deck=False))
    merge_op_changes(result, [_OpChanges(deck=True, study_queues=True, mtime=False)])
    assert result.changes.card and result.changes.deck and result.changes.study_queues
    assert not getattr(result.changes, "mtime", False)
