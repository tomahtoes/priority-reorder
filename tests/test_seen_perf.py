"""Benchmark + parity guard for the boolean `seen:N` model.

`seen:N` is boolean ("seen at all"): production parses each day's dict into a membership set
(`seen_manager.build_seen_day`), unions the window (`_merge_seen_days` -> `SeenWindow`), and
tests each note with `SeenWindow.contains`. This module:

  1. **Parity** — asserts the production boolean equals the counting baseline
     `sum(OccurrenceIndex.get_total(...)) >= 1` for every note under every flag combo. The
     counting `OccurrenceIndex` still backs `occurrences:`, so it is a live cross-check that the
     boolean parser hasn't drifted from the count semantics.
  2. **Speed** — times the seen pipeline in stages and contrasts the boolean model with the
     counting baseline:

        parse | build (x days) | merge/prep | per-note scan | total ex-parse

     `parse` (`json.load` of a day's term_meta_bank) is the unavoidable floor neither model can
     beat. `total ex-parse` is the honest "what counting cost".

Run with timings visible (system Python — the repo .venv lacks pytest):

    C:/Python313/python.exe -m pytest tests/test_seen_perf.py -s

Defaults are scaled down so the test stays cheap in a normal `pytest` run. For representative
numbers, scale up via env vars:

    SEEN_BENCH_ENTRIES=100000 SEEN_BENCH_NOTES=20000 C:/Python313/python.exe -m pytest tests/test_seen_perf.py -s

Set `SEEN_BENCH_REAL=1` to bench against your actual `user_files/_seen/<date>/` dicts (notes are
sampled from the real entries); falls back to synthetic data if none are present.
"""

import json
import math
import os
import random
import time

import dictionary_manager as dm
import seen_manager

try:  # match the package/flat dual-import the production modules use
    from utils import to_hiragana
except ImportError:  # pragma: no cover
    from ..utils import to_hiragana


# ---------------------------------------------------------------------------
# synthetic data generator (deterministic — seeded)
# ---------------------------------------------------------------------------

_KANJI = [chr(c) for c in range(0x4E00, 0x4E00 + 3000)]
_HIRAGANA = [chr(c) for c in range(0x3041, 0x3097)]


def _rand_kanji(rng, n):
    return "".join(rng.choice(_KANJI) for _ in range(n))


def _rand_kana(rng, n):
    return "".join(rng.choice(_HIRAGANA) for _ in range(n))


def _entry(expression, reading, count, is_kana=False):
    """One term_meta_bank entry in the on-disk shape the loader expects."""
    meta = {"reading": reading, "frequency": {"value": count, "displayValue": str(count)}}
    if is_kana:
        meta["displayValue"] = "㋕"  # kana-occurrence marker -> effective expr is the reading
    return [expression, "freq", meta]


def _build_dataset(cfg, rng):
    """Return ``(days_raw, notes)``. ``days_raw`` is ``DAYS`` overlapping samples of a master
    vocabulary (so the window union is realistic); ``notes`` is a ``(expr, reading)`` mix that
    exercises every lookup path — exact / prefix / combine / honorific / miss."""
    n_entries, days, n_notes = cfg["entries"], cfg["days"], cfg["notes"]

    bases = []
    used_expr, used_reading = set(), set()
    while len(bases) < n_entries:
        expr = _rand_kanji(rng, rng.randint(1, 3))
        if expr in used_expr:
            continue
        reading = _rand_kana(rng, rng.randint(2, 4))
        if reading in used_reading:
            continue
        used_expr.add(expr)
        used_reading.add(reading)
        bases.append((expr, reading))

    rel = max(1, n_entries // 10)
    compounds, prefix_bases = [], []
    for i in rng.sample(range(n_entries), rel):
        be = bases[i][0]
        if len(be) >= dm._MIN_PREFIX_LENGTH:
            comp = be + _rand_kanji(rng, 1)
            if comp not in used_expr:
                used_expr.add(comp)
                compounds.append((comp, _rand_kana(rng, rng.randint(2, 4))))
                prefix_bases.append(i)
    honorifics, honor_bases = [], []
    for i in rng.sample(range(n_entries), rel):
        hon = rng.choice(dm._HONORIFIC_PREFIXES) + bases[i][0]
        if hon not in used_expr:
            used_expr.add(hon)
            honorifics.append((hon, _rand_kana(rng, rng.randint(2, 4))))
            honor_bases.append(i)
    kana_bases = rng.sample(range(n_entries), rel)

    # master entry descriptors: (expression, reading, is_kana)
    master = [(e, r, False) for (e, r) in bases]
    master += [(e, r, False) for (e, r) in compounds]
    master += [(e, r, False) for (e, r) in honorifics]
    master += [(bases[i][0], bases[i][1], True) for i in kana_bases]

    day_size = max(1, int(len(master) * 0.85))
    days_raw = [
        [_entry(e, r, rng.randint(1, 50), is_kana) for (e, r, is_kana) in rng.sample(master, day_size)]
        for _ in range(days)
    ]

    notes = []
    notes += [bases[i] for i in rng.sample(range(n_entries), min(n_notes // 4, n_entries))]
    notes += [bases[i] for i in rng.sample(prefix_bases, min(len(prefix_bases), n_notes // 8))]
    notes += [bases[i] for i in rng.sample(honor_bases, min(len(honor_bases), n_notes // 8))]
    # combine path: a (different) kanji card whose reading matches a kana-only entry
    notes += [(_rand_kanji(rng, 2), bases[i][1]) for i in rng.sample(kana_bases, min(len(kana_bases), n_notes // 8))]
    while len(notes) < n_notes:  # misses
        notes.append((_rand_kanji(rng, rng.randint(1, 3)), _rand_kana(rng, rng.randint(2, 4))))
    rng.shuffle(notes)
    return days_raw, notes[:n_notes]


def _real_days(cfg):
    """Load the last ``DAYS`` real seen dicts (env-gated). Returns the raw lists, or ``None`` if
    none are present."""
    today = seen_manager.today_date()
    folders = [seen_manager.date_to_folder(d) for d in seen_manager.window_dates(today, cfg["days"])]
    days_raw = []
    for f in folders:
        data = dm._load_term_meta_raw(seen_manager._seen_dict_name(f))
        if data:
            days_raw.append(data)
    return days_raw or None


def _notes_from_days(days_raw, n_notes, rng):
    """Sample ``(expr, reading)`` candidate notes from real day entries, plus misses."""
    pool = []
    for data in days_raw:
        for entry in data:
            if isinstance(entry, list) and len(entry) >= 3 and isinstance(entry[0], str):
                meta = entry[2]
                reading = meta.get("reading", "") if isinstance(meta, dict) else ""
                pool.append((entry[0], reading if isinstance(reading, str) else ""))
    notes = rng.sample(pool, min(len(pool), n_notes * 3 // 4)) if pool else []
    while len(notes) < n_notes:
        notes.append((_rand_kanji(rng, rng.randint(1, 3)), _rand_kana(rng, rng.randint(2, 4))))
    rng.shuffle(notes)
    return notes[:n_notes]


# ---------------------------------------------------------------------------
# timing helpers
# ---------------------------------------------------------------------------

def _best_of(n, fn):
    """Run ``fn`` ``n`` times, returning ``(last_result, min_ms)``. Only used for side-effect-free
    stages (parse)."""
    best, result = math.inf, None
    for _ in range(n):
        t0 = time.perf_counter()
        result = fn()
        best = min(best, (time.perf_counter() - t0) * 1000)
    return result, best


def _timed(fn):
    """Single-run wall time in ms with the result (used for stages with lazy-build side effects)."""
    t0 = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - t0) * 1000


def _config():
    return {
        "entries": int(os.environ.get("SEEN_BENCH_ENTRIES", 30_000)),
        "days": int(os.environ.get("SEEN_BENCH_DAYS", 7)),
        "notes": int(os.environ.get("SEEN_BENCH_NOTES", 10_000)),
        "real": bool(os.environ.get("SEEN_BENCH_REAL")),
    }


# ---------------------------------------------------------------------------
# the benchmark
# ---------------------------------------------------------------------------

_FLAG_COMBOS = [
    ("none", dict(normalize_kana=False, combine_word_forms=False, prefix_matching=False, honorific_folding=False)),
    ("prefix", dict(normalize_kana=False, combine_word_forms=False, prefix_matching=True, honorific_folding=False)),
    ("all", dict(normalize_kana=True, combine_word_forms=True, prefix_matching=True, honorific_folding=True)),
]


def test_seen_count_vs_boolean_benchmark(capsys, tmp_path):
    cfg = _config()
    rng = random.Random(20260619)

    if cfg["real"]:
        days_raw = _real_days(cfg)
        if days_raw is None:
            days_raw, notes = _build_dataset(cfg, rng)
            source = "synthetic (no real _seen dicts found)"
        else:
            notes = _notes_from_days(days_raw, cfg["notes"], rng)
            source = f"real user_files/_seen ({len(days_raw)} days)"
    else:
        days_raw, notes = _build_dataset(cfg, rng)
        source = "synthetic"

    # parse floor: dump one day and time json.load on it (identical for both models)
    day_path = tmp_path / "term_meta_bank_1.json"
    with open(day_path, "w", encoding="utf-8") as f:
        json.dump(days_raw[0], f, ensure_ascii=False)

    def _load():
        with open(day_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    _, parse_ms = _best_of(3, _load)

    with capsys.disabled():
        print(
            f"\n[seen-perf] source={source} | "
            f"days={len(days_raw)} entries/day~{len(days_raw[0])} notes={len(notes)}"
        )
        print(f"[seen-perf] parse (json.load, 1 day): {parse_ms:6.1f} ms  "
              f"(x{len(days_raw)} days = {parse_ms * len(days_raw):.1f} ms; unavoidable floor)\n")
        header = f"{'flags':6} {'stage':16} {'count(ms)':>10} {'bool(ms)':>10} {'speedup':>8}"
        print(header)
        print("-" * len(header))

        for name, flags in _FLAG_COMBOS:
            nk = flags["normalize_kana"]
            # get_total is query-time and does NOT take normalize_kana (the count index is
            # normalized at build; the query must be folded by the caller, as window_total did).
            get_total_flags = dict(
                combine_word_forms=flags["combine_word_forms"],
                prefix_matching=flags["prefix_matching"],
                honorific_folding=flags["honorific_folding"],
            )

            # ---- counting baseline: build x days -> per-day get_total summation >= 1 ----
            day_indices, c_build = _timed(lambda: [
                dm._build_index_from_raw(
                    d, normalize_kana=flags["normalize_kana"],
                    prefix_matching=flags["prefix_matching"],
                    honorific_folding=flags["honorific_folding"],
                ) for d in days_raw
            ])

            def _count_scan():
                out = []
                for expr, reading in notes:
                    e, r = (to_hiragana(expr), to_hiragana(reading)) if nk else (expr, reading)
                    out.append(sum(di.get_total(e, r, **get_total_flags) for di in day_indices) >= 1)
                return out

            count_results, c_scan = _timed(_count_scan)

            # ---- boolean (production): build x days -> union(+sort) -> contains ----
            bdays, b_build = _timed(lambda: [
                seen_manager.build_seen_day(d, flags["normalize_kana"], flags["honorific_folding"])
                for d in days_raw
            ])

            def _bool_merge():
                w = seen_manager._merge_seen_days(bdays)
                if flags["prefix_matching"]:
                    w._sorted_exprs = sorted(w.exprs)  # one-time prep, attributed to merge
                return w

            bmodel, b_merge = _timed(_bool_merge)

            def _bool_scan():
                return [bmodel.contains(expr, reading, **flags) for expr, reading in notes]

            bool_results, b_scan = _timed(_bool_scan)

            # ---- parity: boolean MUST equal the counting baseline (>= 1) ----
            mismatches = [i for i in range(len(notes)) if count_results[i] != bool_results[i]]
            assert not mismatches, (
                f"parity broken for flags={name}: {len(mismatches)} mismatches, "
                f"first at note {notes[mismatches[0]]} "
                f"(count={count_results[mismatches[0]]} bool={bool_results[mismatches[0]]})"
            )

            c_total = c_build + c_scan
            b_total = b_build + b_merge + b_scan
            rows = [
                ("build x%d" % len(days_raw), c_build, b_build),
                ("merge/prep", 0.0, b_merge),
                ("scan x%d" % len(notes), c_scan, b_scan),
                ("total ex-parse", c_total, b_total),
            ]
            for i, (stage, c, b) in enumerate(rows):
                if stage == "merge/prep":
                    print(f"{name if i == 0 else '':6} {stage:16} {'(none)':>10} {b:10.1f}")
                    continue
                speed = c / b if b > 1e-9 else float("inf")
                mark = "  <-" if stage.startswith("total") else ""
                print(f"{name if i == 0 else '':6} {stage:16} {c:10.1f} {b:10.1f} {speed:7.2f}x{mark}")
            hits = sum(count_results)
            print(f"{'':6} {'(hits: %d/%d)' % (hits, len(notes)):16}")
            print("-" * len(header))
