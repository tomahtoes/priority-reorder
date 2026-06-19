import sys
import types
from datetime import date, datetime

import pytest

import dictionary_manager as dm
import search
import seen_manager


def seen_recorder(calls, ids=None):
    def resolve(n):
        calls.append(n)
        return ids if ids is not None else [7, 8]
    return resolve


def occ_recorder(calls, ids=None):
    def resolve(dict_str, op, thresh):
        calls.append((dict_str, op, thresh))
        return ids if ids is not None else [101, 202]
    return resolve


# --- seen: token rewriting --------------------------------------------------

def test_seen_basic_resolves():
    calls = []
    out = search.rewrite_query("seen:2", seen_resolver=seen_recorder(calls))
    assert out == "(nid:7,8)"
    assert calls == [2]  # bare seen:N -> "appeared in any of the last N days" (boolean)


def test_seen_threshold_removed():
    # The count-threshold syntax is gone: `seen:7` resolves and a trailing `>=10` is left as a
    # normal token (clean removal — not silently absorbed).
    calls = []
    out = search.rewrite_query("seen:7>=10", seen_resolver=seen_recorder(calls))
    assert out == "(nid:7,8)>=10"
    assert calls == [7]


def test_seen_zero_matches_nothing_without_resolving():
    calls = []
    out = search.rewrite_query("seen:0", seen_resolver=seen_recorder(calls))
    assert out == "nid:0"
    assert calls == []  # short-circuited before the resolver


def test_seen_empty_result_is_nid_zero():
    out = search.rewrite_query("seen:2", seen_resolver=lambda n: [])
    assert out == "nid:0"


def test_seen_negation_preserved():
    out = search.rewrite_query("-seen:2", seen_resolver=seen_recorder([]))
    assert out == "-(nid:7,8)"  # leading '-' stays, group negated


def test_seen_fires_inside_parentheses():
    out = search.rewrite_query("(seen:2)", seen_resolver=seen_recorder([]))
    assert out == "((nid:7,8))"


def test_seen_does_not_fire_inside_other_tokens():
    calls = []
    assert search.rewrite_query("unseen:2", seen_resolver=seen_recorder(calls)) == "unseen:2"
    assert search.rewrite_query("deck:seen:2", seen_resolver=seen_recorder(calls)) == "deck:seen:2"
    assert calls == []


def test_seen_combined_with_other_clauses():
    out = search.rewrite_query("deck:JP seen:2 -tag:done", seen_resolver=seen_recorder([]))
    assert out == "deck:JP (nid:7,8) -tag:done"


def test_seen_mixed_with_occurrences_resolve_independently():
    out = search.rewrite_query(
        "occurrences:X>5 seen:2",
        occ_resolver=occ_recorder([], ids=[1]),
        seen_resolver=seen_recorder([], ids=[2]),
    )
    assert out == "(nid:1) (nid:2)"


def test_has_custom_term_includes_seen():
    assert search.has_custom_term("seen:2")
    assert search.has_custom_term("deck:JP seen:7>=10")
    assert not search.has_custom_term("unseen:2")


# --- strip helpers: deliberate asymmetry ------------------------------------

def test_strip_for_candidates_removes_seen_and_custom_terms():
    # The candidate-set base handed to the unpatched find_notes must be free of seen:.
    assert search._strip_for_candidates("deck:JP occurrences:X>5 seen:2").split() == ["deck:JP"]


def test_strip_custom_terms_keeps_seen():
    # The exported stripper leaves seen: in place so the reorder fast path resolves it
    # via the patched find_cards instead of dropping it.
    assert "seen:2" in search._strip_custom_terms("occurrences:X>5 seen:2")


# --- date / window helpers --------------------------------------------------

def test_window_dates():
    today = date(2026, 6, 12)
    assert seen_manager.window_dates(today, 1) == [today]
    assert seen_manager.window_dates(today, 2) == [today, date(2026, 6, 11)]
    assert seen_manager.window_dates(today, 0) == []
    assert seen_manager.window_dates(today, -3) == []


def test_date_to_folder():
    assert seen_manager.date_to_folder(date(2026, 6, 12)) == "2026-06-12"


def test_today_date_before_rollover_is_previous_calendar_day():
    # 2am with a 4am rollover still counts as the previous day (added:/edited: semantics).
    assert seen_manager.today_date(now=datetime(2026, 6, 12, 2, 0), rollover=4) == date(2026, 6, 11)


def test_today_date_after_rollover_is_same_day():
    assert seen_manager.today_date(now=datetime(2026, 6, 12, 10, 0), rollover=4) == date(2026, 6, 12)


# --- SeenWindow.contains: presence + parity with counting -------------------

def _build_window(day_raws, normalize_kana=False, honorific_folding=False):
    days = [seen_manager.build_seen_day(d, normalize_kana, honorific_folding) for d in day_raws]
    return seen_manager._merge_seen_days(days)


def test_seen_window_presence_and_prefix():
    window = _build_window([[["下駄箱", "freq", 5]], [["下駄", "freq", 2]]])
    assert window.contains("下駄", "")       # exact, on the previous day
    assert window.contains("下駄箱", "")     # exact, on the recent day
    assert not window.contains("茶", "")     # absent
    # prefix: 下駄 is credited by the longer 下駄箱 even when the exact 下駄 day is absent
    one = _build_window([[["下駄箱", "freq", 5]]])
    assert not one.contains("下駄", "")
    assert one.contains("下駄", "", prefix_matching=True)


def test_seen_window_combine_word_forms():
    # A kana-only entry is keyed under the reading; a kanji card sharing that reading is only
    # credited with combine_word_forms.
    kana_only = _build_window([[["なんきん", "freq", {"value": 3}]]])
    assert not kana_only.contains("南京", "なんきん")
    assert kana_only.contains("南京", "なんきん", combine_word_forms=True)


def test_seen_window_honorific_folding():
    # お茶 present + 茶 present -> honorific folding makes the bare 茶 (and an unseen-as-kanji
    # card) credited. Here a card present ONLY via the honorific fold:
    window = _build_window([[["お得", "freq", 5]]], honorific_folding=True)
    assert not window.contains("得", "")  # 得 itself never appeared
    window2 = _build_window([[["お得", "freq", 5], ["得", "freq", 1]]], honorific_folding=True)
    assert window2.contains("得", "", honorific_folding=True)


def _ref_seen(day_indices, e, r, **flags):
    """Reference presence from the counting index: seen iff the summed total is >= 1."""
    return sum(d.get_total(e, r, **flags) for d in day_indices) >= 1


def test_seen_contains_matches_counting_presence_incl_homograph():
    # SeenWindow.contains must equal the counting index's `get_total(...) >= 1` for EVERY
    # card/flag combo — the boolean model is a faithful drop-in for bare `seen:N`. Reuses the
    # homograph dataset (角 read かど vs つの) whose per-day reading-mismatch fallback a full
    # count merge would drop, to prove presence is unaffected by it.
    raws = [
        [
            ["下駄", "freq", {"reading": "げた", "frequency": {"value": 5}}],
            ["下駄箱", "freq", {"reading": "げたばこ", "frequency": {"value": 10}}],
            ["角", "freq", {"reading": "かど", "frequency": {"value": 4}}],
            ["お茶", "freq", {"reading": "おちゃ", "frequency": {"value": 50}}],
            ["茶", "freq", {"reading": "ちゃ", "frequency": {"value": 8}}],
        ],
        [
            ["下駄箱", "freq", {"reading": "げたばこ", "frequency": {"value": 6}}],
            ["角", "freq", {"reading": "つの", "frequency": {"value": 3}}],
        ],
        [
            ["下駄", "freq", {"reading": "げた", "frequency": {"value": 2}}],
            ["下駄屋", "freq", {"reading": "げたや", "frequency": {"value": 8}}],
        ],
    ]
    cards = [("下駄", "げた"), ("角", "かど"), ("茶", "ちゃ"), ("下駄箱", "げたばこ"),
             ("お茶", "おちゃ"), ("ない", "")]
    flagsets = [
        {},
        {"prefix_matching": True},
        {"combine_word_forms": True},
        {"prefix_matching": True, "combine_word_forms": True},
        {"honorific_folding": True},
        {"prefix_matching": True, "combine_word_forms": True, "honorific_folding": True},
    ]
    for fs in flagsets:
        honor = fs.get("honorific_folding", False)
        window = _build_window(raws, honorific_folding=honor)
        day_indices = [dm._build_index_from_raw(r, honorific_folding=honor) for r in raws]
        for e, r in cards:
            assert window.contains(e, r, **fs) == _ref_seen(day_indices, e, r, **fs), (e, r, fs)


# --- reservation: `seen` is never a normal occurrence dict ------------------

def test_get_all_dict_names_excludes_seen(monkeypatch):
    monkeypatch.setattr(dm.os, "listdir", lambda p: ["A", "_seen", "B", ".tmp", "all"])
    monkeypatch.setattr(dm.os.path, "isdir", lambda p: True)
    assert dm.get_all_dict_names() == ["A", "B"]


def test_expand_dict_names_drops_seen():
    assert dm.expand_dict_names("_seen") == []
    assert dm.expand_dict_names("[A,_seen,B]") == ["A", "B"]


def test_expand_all_keyword_never_includes_seen(monkeypatch):
    # Belt-and-suspenders: even if enumeration leaked it, expand drops it.
    monkeypatch.setattr(dm, "get_all_dict_names", lambda: ["D1", "_seen", "D2"])
    assert dm.expand_dict_names("all") == ["D1", "D2"]


def test_updater_dict_dirs_excludes_seen(monkeypatch):
    pytest.importorskip("requests")
    import updater

    monkeypatch.setattr(updater.os, "listdir", lambda p: ["A", "_seen", "B"])
    monkeypatch.setattr(updater.os.path, "isdir", lambda p: True)
    u = updater.JitenUpdater()
    assert sorted(u._dict_dirs()) == ["A", "B"]


# --- resolve_seen performance regression guards -----------------------------
#
# The window (and the filesystem stat that checks for current-day rewrites) must be
# resolved ONCE per search, not once per note per day — the bug that made `seen:`
# searches and reorders pathologically slow. And repeated full scans in one reorder
# must be served from the memo.

def _install_fake_seen_env(monkeypatch, note_count=500):
    import config_manager
    from config_manager import Config

    monkeypatch.setattr(config_manager, "get_config", lambda: Config())
    aqt = sys.modules["aqt"]
    monkeypatch.setattr(
        aqt.mw, "col",
        types.SimpleNamespace(mod=1, get_config=lambda *a: 4),
        raising=False,
    )
    monkeypatch.setattr(seen_manager.dm, "_load_term_meta_raw", lambda name: None)  # empty days
    seen_manager.clear_cache()
    search._seen_cache.clear()
    monkeypatch.setattr(search, "_seen_sig", None, raising=False)
    return [(i, {"Expression": f"e{i}", "ExpressionReading": ""}) for i in range(note_count)]


def test_resolve_seen_stats_window_once_not_per_note(monkeypatch):
    notes = _install_fake_seen_env(monkeypatch, note_count=500)
    monkeypatch.setattr(search, "_iter_candidate_notes", lambda fields, cand=None: iter(notes))

    stats = {"n": 0}

    def counting_source_mtime(folder):
        stats["n"] += 1
        return 111.0

    monkeypatch.setattr(seen_manager, "_source_mtime", counting_source_mtime)

    search.resolve_seen(7, candidate_nids=None)

    # O(window): get_seen_window (7 stats) + the memo signature's window_mtimes (7).
    # Pre-fix this re-statted per note per day -> 7 * 500 = 3500.
    assert stats["n"] <= 14


def test_resolve_seen_memoizes_full_scan_within_signature(monkeypatch):
    notes = _install_fake_seen_env(monkeypatch, note_count=50)

    scans = {"n": 0}

    def counting_iter(fields, cand=None):
        scans["n"] += 1
        return iter(notes)

    monkeypatch.setattr(search, "_iter_candidate_notes", counting_iter)
    monkeypatch.setattr(seen_manager, "_source_mtime", lambda folder: 111.0)

    search.resolve_seen(7, candidate_nids=None)
    search.resolve_seen(7, candidate_nids=None)

    assert scans["n"] == 1  # second identical full scan served from _seen_cache
