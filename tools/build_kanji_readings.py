"""Generate kanji_readings.txt from KANJIDIC2.

KANJIDIC2 is the property of the Electronic Dictionary Research and Development
Group (EDRDG) and is used under the Creative Commons Attribution-ShareAlike 4.0
licence: https://www.edrdg.org/wiki/index.php/KANJIDIC_Project

EDRDG's licence requires "a procedure for regular updating of the data from the
most recent versions available". That is this script, and re-running it is a
release step (see AGENTS.md). A one-time import would put the addon out of
compliance the moment KANJIDIC changes.

Usage:
    python tools/build_kanji_readings.py               # download + build
    python tools/build_kanji_readings.py --readable    # also emit a decoded copy
    python tools/build_kanji_readings.py --nanori      # include name readings
"""

import argparse
import gzip
import io
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from kanji_readings import (  # noqa: E402
    encode_kana, decode_kana, _KANA, _SEP_GROUP,
)
from utils import to_hiragana  # noqa: E402

KANJIDIC_URL = "http://www.edrdg.org/kanjidic/kanjidic2.xml.gz"
DEFAULT_OUT = os.path.join(ROOT, "kanji_readings.txt")

HEADER = [
    "# kanji_readings.txt: per-kanji readings derived from KANJIDIC2.",
    "#",
    "# Source: KANJIDIC2, Copyright (C) Electronic Dictionary Research and",
    "# Development Group (EDRDG). Used under CC BY-SA 4.0.",
    "# https://www.edrdg.org/wiki/index.php/KANJIDIC_Project",
    "#",
    "# THIS FILE IS A MODIFIED EXTRACT, not KANJIDIC2 itself. Changes made:",
    "# only ja_on/ja_kun readings kept; on'yomi folded katakana->hiragana;",
    "# KANJIDIC's -prefix/-suffix markers stripped; readings grouped by stem;",
    "# kana encoded one ASCII byte each. This file is likewise CC BY-SA 4.0.",
    "# Regenerate with tools/build_kanji_readings.py.",
    "#",
    "# Format, one line per kanji, no separator after the kanji:",
    "#   <kanji><stem>|<stem>|...",
    "#   group = <stem>            bare reading, no okurigana",
    "#         = <stem>:<o>,<o>    okurigana options; an empty option means the",
    "#                             bare stem is also valid",
    "# Kana are ASCII-encoded; decode with kanji_readings.decode_kana, or:",
    "#   python -c \"import sys;sys.path.insert(0,'.');import kanji_readings as k;\\",
    "#   print(k.decode_kana(open('kanji_readings.txt',encoding='utf-8').read()))\"",
]


def fetch(path=None):
    if path:
        with open(path, "rb") as f:
            blob = f.read()
    else:
        sys.stderr.write("downloading %s ...\n" % KANJIDIC_URL)
        with urllib.request.urlopen(KANJIDIC_URL) as r:
            blob = r.read()
    return gzip.decompress(blob).decode("utf-8")


def split_reading(reading):
    """A KANJIDIC reading -> (stem, okurigana or None).

    Applied to on'yomi and kun'yomi alike:

    - katakana folds to hiragana. On'yomi are written in katakana, and a
      handful of kun readings are too: the unit kanji, 吋/インチ, 瓩/キログラム.
      The matcher folds the card's reading the same way, so both sides meet.
    - the -prefix/-suffix markers ('-り', 'お-') are stripped: they record where
      the reading attaches, which the matcher works out positionally anyway.
      On'yomi carry them too, not just kun.
    - the '.' okurigana boundary splits stem from okurigana."""
    reading = to_hiragana(reading).strip("-")
    if "." in reading:
        stem, _, okuri = reading.partition(".")
        return stem, okuri
    return reading, None


def parse(xml_text, include_nanori=False):
    """kanji -> ordered {stem: [okurigana options]}. An empty-string option
    means the bare stem is valid on its own."""
    out = {}
    bad = set()
    valid = set(_KANA)
    for _, el in ET.iterparse(io.StringIO(xml_text), events=("end",)):
        if el.tag != "character":
            continue
        literal = el.findtext("literal")
        raw = []
        for r in el.iter("reading"):
            kind = r.get("r_type")
            if kind in ("ja_on", "ja_kun"):
                raw.append(split_reading(r.text))
        if include_nanori:
            for n in el.iter("nanori"):
                raw.append(split_reading(n.text))
        el.clear()

        groups = {}
        for stem, okuri in raw:
            if not stem:
                continue
            unknown = [c for c in stem + (okuri or "") if c not in valid]
            if unknown:
                bad.update(unknown)
                continue
            opts = groups.setdefault(stem, [])
            opt = okuri or ""
            if opt not in opts:
                opts.append(opt)
        if groups:
            out[literal] = groups
    return out, bad


def render(table):
    """One line per kanji: the kanji, then its distinct reading stems.

    Okurigana is deliberately dropped. It was only ever used to require that the
    expression continue with it, and the matcher's reading alignment already
    disambiguates 明るい (明=あか) from 明ける (明=あ) without it, while the
    check wrongly rejected 連用形 compounds (引き取る, 見送る) whose conjugation
    changes the okurigana."""
    lines = []
    for kanji, groups in table.items():
        lines.append(kanji + _SEP_GROUP.join(encode_kana(s) for s in groups))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kanjidic", help="local kanjidic2.xml.gz (default: download)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--nanori", action="store_true", help="include name readings")
    ap.add_argument("--readable", action="store_true", help="also write a decoded copy")
    args = ap.parse_args()

    xml_text = fetch(args.kanjidic)
    table, bad = parse(xml_text, include_nanori=args.nanori)
    lines = render(table)
    body = "\n".join(HEADER + lines) + "\n"
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)

    if args.readable:
        alt = args.out.replace(".txt", ".readable.txt")
        with open(alt, "w", encoding="utf-8", newline="\n") as f:
            f.write(decode_kana(body))
        sys.stderr.write("wrote %s\n" % alt)

    readings = sum(len(o) for g in table.values() for o in g.values())
    stems = sum(len(g) for g in table.values())
    raw_utf8 = len(decode_kana("\n".join(lines)).encode("utf-8"))
    enc_utf8 = len("\n".join(lines).encode("utf-8"))
    sys.stderr.write(
        "kanji with readings : %d\n"
        "distinct stems      : %d (%.1f per kanji)\n"
        "reading variants    : %d\n"
        "payload, kana UTF-8 : %d bytes\n"
        "payload, ASCII-enc  : %d bytes (%.2fx smaller)\n"
        "file on disk        : %d bytes\n"
        % (len(table), stems, stems / max(1, len(table)), readings,
           raw_utf8, enc_utf8, raw_utf8 / max(1, enc_utf8), len(body.encode("utf-8")))
    )
    if bad:
        sys.stderr.write("WARNING: skipped readings containing %r\n" % sorted(bad))


if __name__ == "__main__":
    main()
