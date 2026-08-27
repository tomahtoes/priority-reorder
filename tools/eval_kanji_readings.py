"""Score kanji_readings.reading_slots against JmdictFurigana.

JmdictFurigana (https://github.com/Doublevil/JmdictFurigana, CC BY-SA 4.0) hand-
curates which kana belong to which kanji for ~178k JMdict entries. It is not
shipped with the addon -- it is far too big, and it only covers dictionary words
-- but it is the right oracle for asking how often our per-kanji matcher agrees
with a human-checked answer.

The comparison is structural, because we deliberately record *base* readings
(鼻血 -> 血=ち) while JmdictFurigana records *surfaces* (血=ぢ). What both sides
agree on is which kanji can be given a reading of their own at all:

  false ateji   we leave a kanji unresolved where JmdictFurigana assigns it its
                own furigana. Over-firing: the card is prioritised when it did
                not need to be. The recall-favouring direction.
  missed ateji  we resolve a kanji that JmdictFurigana can only cover with a
                merged span. Under-firing: a genuinely irregular reading is not
                flagged. Less visible, and the one worth watching.

Usage:
    python tools/eval_kanji_readings.py                 # download + score
    python tools/eval_kanji_readings.py --limit 20000   # quick pass
"""

import argparse
import collections
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import kanji_readings as kr  # noqa: E402
from utils import is_kanji  # noqa: E402

FURIGANA_URL = (
    "https://github.com/Doublevil/JmdictFurigana/releases/latest/download/"
    "JmdictFurigana.txt"
)


def fetch(path=None):
    if path and os.path.exists(path):
        return open(path, encoding="utf-8").read()
    sys.stderr.write("downloading JmdictFurigana ...\n")
    with urllib.request.urlopen(FURIGANA_URL) as r:
        text = r.read().decode("utf-8")
    if path:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return text


def parse_line(line):
    """'大人買い|おとながい|0-1:おとな;2:が' -> (word, reading, {index: is_merged}).

    A single-index entry means JmdictFurigana could give that kanji a reading of
    its own; an a-b range means it could not split the span, which is its way of
    recording jukujikun."""
    parts = line.split("|")
    if len(parts) != 3:
        return None
    word, reading, spec = parts
    merged = {}
    for chunk in spec.split(";"):
        pos, _, _kana = chunk.partition(":")
        if "-" in pos:
            lo, _, hi = pos.partition("-")
            try:
                lo, hi = int(lo), int(hi)
            except ValueError:
                return None
            for i in range(lo, hi + 1):
                merged[i] = (hi > lo)
        else:
            try:
                merged[int(pos)] = False
            except ValueError:
                return None
    return word, reading, merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--furigana", help="local JmdictFurigana.txt (downloaded if absent)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--examples", type=int, default=12)
    args = ap.parse_args()

    text = fetch(args.furigana)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if args.limit:
        lines = lines[:args.limit]

    words = kanji = agree = false_ateji = missed_ateji = 0
    skipped = 0
    false_by_kanji = collections.Counter()
    missed_by_kanji = collections.Counter()
    false_examples, missed_examples = [], []

    for line in lines:
        parsed = parse_line(line)
        if parsed is None:
            skipped += 1
            continue
        word, reading, merged = parsed
        positions = [i for i, ch in enumerate(word) if is_kanji(ch)]
        if not positions:
            continue
        slots = kr.reading_slots(word, reading)
        if len(slots) != len(positions):
            # 々 expansion changes the kanji count; not comparable position-wise.
            skipped += 1
            continue
        words += 1
        for slot, pos in zip(slots, positions):
            kanji += 1
            ours_unresolved = kr._UNRESOLVED in slot
            theirs_merged = merged.get(pos)
            if theirs_merged is None:
                skipped += 1
                continue
            if ours_unresolved == theirs_merged:
                agree += 1
            elif ours_unresolved:
                false_ateji += 1
                false_by_kanji[word[pos]] += 1
                if len(false_examples) < args.examples:
                    false_examples.append((word, reading, word[pos]))
            else:
                missed_ateji += 1
                missed_by_kanji[word[pos]] += 1
                if len(missed_examples) < args.examples:
                    missed_examples.append((word, reading, word[pos]))

    compared = agree + false_ateji + missed_ateji or 1
    print("words compared      : %d" % words)
    print("kanji compared      : %d" % compared)
    print("agreement           : %d (%.2f%%)" % (agree, 100.0 * agree / compared))
    print("false ateji (over)  : %d (%.2f%%)" % (false_ateji, 100.0 * false_ateji / compared))
    print("missed ateji (under): %d (%.2f%%)" % (missed_ateji, 100.0 * missed_ateji / compared))
    print("skipped             : %d" % skipped)

    def show(title, counter, examples):
        print("\n%s" % title)
        print("  worst kanji: %s" % " ".join(
            "%s(%d)" % (k, n) for k, n in counter.most_common(15)))
        for word, reading, ch in examples:
            print("  %-10s %-12s  %s -> %s" % (
                word, reading, ch,
                "  ".join(kr.describe_slot(s) for s in kr.reading_slots(word, reading))))

    if false_by_kanji:
        show("FALSE ATEJI (we wildcard, JmdictFurigana splits)", false_by_kanji, false_examples)
    if missed_by_kanji:
        show("MISSED ATEJI (we split, JmdictFurigana merges)", missed_by_kanji, missed_examples)


if __name__ == "__main__":
    main()
