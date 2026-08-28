"""Per-kanji reading resolution for the `kanji:new_reading` search term.

Given a word and its kana reading, work out which reading each kanji contributes,
so a "reading you have not learned yet" can be counted the same way
kanji_manager counts kanji you have not learned yet.

The data is a KANJIDIC2-derived table of per-kanji readings (see
tools/build_kanji_readings.py). Nothing here needs a word list: resolution is
per-kanji, so novel compounds, conjugated forms and names all work as long as
their kanji use listed readings. A reading that no listed reading explains is
covered by a wildcard span, which is exactly the jukujikun/ateji case and is
counted as new by definition.
"""

import os
import re
from typing import Dict, List, Optional, Tuple

try:  # inside Anki: isolated package namespace
    from .utils import is_kanji, to_hiragana
except ImportError:  # pytest / flat-import context
    from utils import is_kanji, to_hiragana


# kana <-> ASCII codec
#
# Readings are stored one ASCII byte per kana rather than three UTF-8 bytes.
# That is the single biggest lever on the shipped table (~450 KB -> ~200 KB),
# and it also makes every comparison on the matcher's hot path run against a
# pure-ASCII str, which CPython stores at 1 byte/char instead of the UCS-2
# representation any kana string forces.
#
# The mapping is positional and therefore stable across KANJIDIC updates:
# U+3041..U+3096 (the hiragana block) followed by the long-vowel mark.
_KANA = "".join(chr(c) for c in range(0x3041, 0x3097)) + "ー"

# Separators used by the on-disk format, kept out of the kana alphabet.
_SEP_GROUP = "|"   # between readings
_UNRESOLVED = "*"  # marks a slot the reading table could not explain

# Every reserved character is held out of the kana alphabet. "*" in particular:
# an encoded stem containing it would be indistinguishable from an unresolved
# slot, and unresolved_count would silently miscount every word whose reading
# used that kana.
_RESERVED = _SEP_GROUP + _UNRESOLVED

_ASCII = "".join(
    chr(c) for c in range(0x21, 0x7F) if chr(c) not in _RESERVED
)[:len(_KANA)]

if len(_ASCII) < len(_KANA):  # pragma: no cover - alphabet is fixed
    raise RuntimeError("ASCII alphabet too small for the kana set")

_ENCODE_MAP = {ord(k): a for k, a in zip(_KANA, _ASCII)}
_DECODE_MAP = {ord(a): k for k, a in zip(_KANA, _ASCII)}


def encode_kana(text: str) -> str:
    """Kana -> the one-byte-per-kana ASCII form. Characters outside the kana
    block (kanji, latin, punctuation) pass through untouched, so an expression
    can be translated with the same call as a reading."""
    return text.translate(_ENCODE_MAP)


def decode_kana(text: str) -> str:
    """Inverse of encode_kana. Only needed for display and for the dev tools,
    the matcher and the slot keys stay in the ASCII domain end to end."""
    return text.translate(_DECODE_MAP)


# surface variants
#
# A listed reading shows up in a real word in several shapes. Rather than
# predicting which shape a compound *should* take, we generate the shapes a
# reading may legally take and let the observed reading pick. Each variant
# carries the guard that makes it legal, so the matcher can apply it
# positionally instead of re-deriving it.

_F_RENDAKU = 1  # first mora voiced: legal only when something precedes it
_F_SOKUON = 2   # ends in っ: legal only when something follows it

# 連濁 voices the first mora only. は-row takes both dakuten and handakuten
# (三本 さんぼん vs 一本 いっぽん), so it yields two variants.
_VOICING = {
    "か": "が", "き": "ぎ", "く": "ぐ", "け": "げ", "こ": "ご",
    "さ": "ざ", "し": "じ", "す": "ず", "せ": "ぜ", "そ": "ぞ",
    "た": "だ", "ち": "ぢ", "つ": "づ", "て": "で", "と": "ど",
    "は": "ば", "ひ": "び", "ふ": "ぶ", "へ": "べ", "ほ": "ぼ",
}
_HANDAKU = {"は": "ぱ", "ひ": "ぴ", "ふ": "ぷ", "へ": "ぺ", "ほ": "ぽ"}

_VOICE_MAP = {}
for _k, _v in _VOICING.items():
    _VOICE_MAP.setdefault(encode_kana(_k), []).append(encode_kana(_v))
for _k, _v in _HANDAKU.items():
    _VOICE_MAP.setdefault(encode_kana(_k), []).append(encode_kana(_v))

_SOKUON = encode_kana("っ")
_U = encode_kana("う")
# 促音便 clips these to っ: 学(がく)校 -> がっこう, 一(いち)本 -> いっぽん.
_CLIPPABLE = frozenset(encode_kana(c) for c in "つちくき")


def _surface_variants(stem):
    """[(surface, flags)] for one stem, base form first."""
    out = [(stem, 0)]
    if not stem:
        return out
    voiced = _VOICE_MAP.get(stem[0])
    bases = [(stem, 0)]
    if voiced:
        for v in voiced:
            bases.append((v + stem[1:], _F_RENDAKU))
    for base, flags in bases:
        if base[-1] in _CLIPPABLE:
            # 促音便: the final mora clips to っ.
            out.append((base[:-1] + _SOKUON, flags | _F_SOKUON))
        elif base.endswith(_U) and len(base) > 1:
            # じゅう -> じゅっ / じっ (十本 じっぽん).
            out.append((base[:-1] + _SOKUON, flags | _F_SOKUON))
            if len(base) > 2:
                out.append((base[:-2] + _SOKUON, flags | _F_SOKUON))
        # 促音添加: a trailing っ can be added outright, which is what 三日
        # みっか (三=み + っ + 日=か) and 真っ赤 まっか need, since neither stem ends
        # in a clippable mora, so the rule above never reaches them.
        out.append((base + _SOKUON, flags | _F_SOKUON))
        if flags:
            out.append((base, flags))
    seen = set()
    uniq = []
    for surf, flags in out:
        if (surf, flags) not in seen:
            seen.add((surf, flags))
            uniq.append((surf, flags))
    return uniq


# table loading

_DATA_FILE = "kanji_readings.txt"

_TABLE = None            # kanji -> raw payload, loaded on first use
_EXPANDED = {}           # kanji -> (variants, maxlen), built on first touch
_LOAD_FAILED = False


def _load():
    """kanji -> raw payload. Read on first use, never at import: a collection
    that never runs a new_reading term pays nothing for this file."""
    global _TABLE, _LOAD_FAILED
    if _TABLE is not None:
        return _TABLE
    if _LOAD_FAILED:
        return {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _DATA_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        # Degrade to "no readings known" rather than breaking the reorder; every
        # kanji then reads as unresolved, which the diagnostic will report.
        print("[priority-reorder] kanji reading table unavailable: %s" % e)
        _LOAD_FAILED = True
        return {}
    # Header lines start with '#'; data lines start with the kanji itself, so
    # {line[0]: line[1:]} keys the table without a separator column.
    _TABLE = {
        line[0]: line[1:]
        for line in text.split("\n")
        if line and line[0] != "#"
    }
    return _TABLE


def _expand(kanji):
    """(variants, maxlen) for one kanji, where variants maps a surface form to
    [(stem, flags)]. Built on first touch and cached: expanding all ~12k kanji up
    front would cost far more than the handful a collection actually contains.

    Only the stem is stored. KANJIDIC's okurigana (た.べる) was used at one point
    to require that the expression continue with べる, but the reading alignment
    already separates 明るい/あかるい (明=あか) from 明ける/あける (明=あ) on its
    own, while the okurigana check wrongly rejected every 連用形 compound --
    引き取る, 見送る, 続き, because conjugation changes the okurigana. Dropping
    it took unresolved kanji from 9.9% to 3.6% on a 26k-word sample."""
    entry = _EXPANDED.get(kanji)
    if entry is not None:
        return entry
    payload = _load().get(kanji)
    if payload is None:
        _EXPANDED[kanji] = ()
        return ()
    variants = {}
    maxlen = 0
    for stem in payload.split(_SEP_GROUP):
        for surf, flags in _surface_variants(stem):
            variants.setdefault(surf, []).append((stem, flags))
            if len(surf) > maxlen:
                maxlen = len(surf)
    entry = (variants, maxlen)
    _EXPANDED[kanji] = entry
    return entry


# field normalisation

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&(?:[a-zA-Z]+|#\d+);")
# Anki furigana: 食[た]べる / 火[や]傷[けど]. The bracketed kana IS the reading,
# so a reading field holding markup is an asset, not a problem. Without this
# every such note would wildcard completely and the term would match everything.
_FURIGANA_RE = re.compile(r"[^\s\[\]]*\[([^\[\]]*)\]")

_ITERATION = "々"
# ヶ/ヵ are the 箇 counter abbreviation, always read か (一ヶ月 いっかげつ). After
# katakana folding they land on ゕ/ゖ, which would otherwise never match.
_SMALL_KA = {0x3095: "か", 0x3096: "か"}

_MAX_EXPR = 12
_MAX_READ = 24


def _clean(text):
    """Strip the markup a reading field may carry. Scoped to this term: the rest
    of the addon deliberately treats field values as opaque."""
    if not text:
        return ""
    if "<" in text:
        text = _TAG_RE.sub("", text)
    if "&" in text:
        text = _ENTITY_RE.sub(" ", text)
    if "[" in text:
        text = _FURIGANA_RE.sub(lambda m: m.group(1), text)
    return text.strip()


def _norm_expr(text):
    text = to_hiragana(_clean(text)).translate(_SMALL_KA)
    if _ITERATION in text:
        out = []
        for ch in text:
            out.append(out[-1] if ch == _ITERATION and out else ch)
        text = "".join(out)
    return encode_kana(text)


def _norm_read(text):
    return encode_kana(to_hiragana(_clean(text)))


# the matcher

_INF = 1 << 30


def _exact(expr, read, memo, i=0, j=0):
    """Slots for a parse that explains every kanji, or None if none exists.
    Tried first because most words are regular and this is much cheaper than the
    wildcard search."""
    key = (i, j)
    if key in memo:
        return memo[key]
    n, m = len(expr), len(read)
    res = None
    if i == n:
        res = () if j == m else None
    else:
        ch = expr[i]
        if is_kanji(ch):
            exp = _expand(ch)
            if exp:
                variants, maxlen = exp
                for length in range(1, min(maxlen, m - j) + 1):
                    cands = variants.get(read[j:j + length])
                    if not cands:
                        continue
                    for stem, flags in cands:
                        if flags & _F_RENDAKU and j == 0:
                            continue
                        if flags & _F_SOKUON and j + length >= m:
                            continue
                        sub = _exact(expr, read, memo, i + 1, j + length)
                        if sub is not None:
                            res = (ch + stem,) + sub
                            break
                    if res is not None:
                        break
        elif j < m and read[j] == ch:
            res = _exact(expr, read, memo, i + 1, j + 1)
    memo[key] = res
    return res


def _wild(expr, read, memo, i=0, j=0):
    """(cost, slots) minimising the number of kanji left unexplained. A wildcard
    span is always available, so every word parses. "Failure" becomes "wildcard
    used", which is exactly the jukujikun/ateji case."""
    key = (i, j)
    hit = memo.get(key)
    if hit is not None:
        return hit
    n, m = len(expr), len(read)
    if i == n:
        best = (0, ()) if j == m else (_INF, ())
        memo[key] = best
        return best
    best = (_INF, ())
    ch = expr[i]
    if is_kanji(ch):
        exp = _expand(ch)
        if exp:
            variants, maxlen = exp
            for length in range(1, min(maxlen, m - j) + 1):
                cands = variants.get(read[j:j + length])
                if not cands:
                    continue
                for stem, flags in cands:
                    if flags & _F_RENDAKU and j == 0:
                        continue
                    if flags & _F_SOKUON and j + length >= m:
                        continue
                    cost, sub = _wild(expr, read, memo, i + 1, j + length)
                    if cost < best[0]:
                        best = (cost, (ch + stem,) + sub)
        run = 1
        while i + run < n and is_kanji(expr[i + run]):
            run += 1
        # Widest run first so that, among equal-cost parses, the whole
        # unexplainable stretch becomes ONE span: 今日 gives 今*きょう / 日*きょう
        # rather than splitting きょう at a non-mora boundary into き / ょう.
        for span_kanji in range(run, 0, -1):
            # A kanji rarely accounts for more than a few kana; the cap keeps the
            # search from exploring spans no real reading would produce.
            cap = min(m - j, 4 * span_kanji + 2)
            for span_kana in range(1, cap + 1):
                cost, sub = _wild(expr, read, memo, i + span_kanji, j + span_kana)
                if cost >= _INF:
                    continue
                cost += span_kanji
                if cost < best[0]:
                    # The span is part of the slot key, so learning 火傷 credits
                    # 火*やけど and it never fires again, while another ateji word
                    # using 火 gets a different span and still does.
                    span = read[j:j + span_kana]
                    best = (
                        cost,
                        tuple(expr[i + t] + "*" + span for t in range(span_kanji)) + sub,
                    )
    elif j < m and read[j] == ch:
        best = _wild(expr, read, memo, i + 1, j + 1)
    memo[key] = best
    return best


# public API

# Bounded like dictionary_manager's combined memo: evict oldest-first rather
# than clearing wholesale, which would thrash once the working set exceeds it.
_MEMO_CAP = 50_000
_MEMO = {}

# A slot key is `kanji + stem` for a resolved reading, or `kanji + "*" + span`
# for one the table cannot explain. Stems are kana, encoded to ASCII, so they can
# never contain "*" (see _RESERVED), which makes unresolved slots detectable
# with a substring test and needs no separate return value.


def reading_slots(expression, reading):
    """One slot key per kanji occurrence in `expression`, in order.

    Positions, not distinct kanji, matching kanji_manager._extract_kanji: 日曜日
    yields a slot for each 日, and they differ (にち vs び). Returns () when the
    expression has no kanji, or when there is no usable reading. Callers must
    treat that as "no information", never as "nothing new"."""
    key = (expression, reading)
    hit = _MEMO.get(key)
    if hit is not None:
        return hit
    expr = _norm_expr(expression)
    read = _norm_read(reading)
    kanji = [ch for ch in expr if is_kanji(ch)]
    if not kanji or not read:
        slots = ()
    elif len(expr) > _MAX_EXPR or len(read) > _MAX_READ:
        # A sentence pasted into the expression field would blow up the search.
        slots = tuple(ch + _UNRESOLVED for ch in kanji)
    else:
        slots = _exact(expr, read, {})
        if slots is None:
            cost, slots = _wild(expr, read, {})
            if cost >= _INF:
                # No parse reaches the end at all (the reading disagrees with the
                # expression's kana). Treat the whole reading as one span.
                slots = tuple(ch + _UNRESOLVED + read for ch in kanji)
    if len(_MEMO) >= _MEMO_CAP:
        _MEMO.pop(next(iter(_MEMO)))
    _MEMO[key] = slots
    return slots


# When more than this fraction of checked kanji come back unresolved, the
# reading field is almost certainly not the reading field. On a 26k-word sample
# of real vocabulary ~4% of kanji are unresolved and ~6% of words carry at least
# one, and the JmdictFurigana comparison agrees within a few points, so 60% is
# far outside anything Japanese produces. The sample floor stops a tiny deck of
# jukujikun from tripping it.
UNRESOLVED_WARN_RATE = 0.6
UNRESOLVED_MIN_SAMPLE = 50


def unresolved_count(slots):
    """How many slots the reading table could not explain. Used for the
    misconfiguration diagnostic: a reading field holding the wrong data
    wildcards everything, and the term then matches essentially every card
    without raising anything."""
    return sum(1 for s in slots if _UNRESOLVED in s)


def describe_slot(slot):
    """Human-readable form of a slot key, for the summary window and debugging."""
    kanji, sep, rest = slot.partition(_UNRESOLVED)
    if sep:
        return "%s=?%s" % (kanji, decode_kana(rest)) if rest else "%s=?" % kanji
    return "%s=%s" % (slot[0], decode_kana(slot[1:]))


def clear_cache():
    """Drop the per-(expression, reading) memo. Wired to the profile switch
    alongside data_manager.clear_note_cache."""
    _MEMO.clear()
