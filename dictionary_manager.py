"""Occurrence counting over Yomitan-style term-meta dictionaries.

The `_is_*_entry`, `_variant_kanji_compatible`, `_kanji_skeleton`, `_suru_reading_matches`
and `_stem_candidates` helpers are shared with seen_manager, which implements the boolean
(`seen:N`) twin of every counting rule here. Editing one side without the other makes the
two drift. A drift-guard test pins them against each other.
"""

import bisect
import json
import os
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

try:  # inside Anki: isolated package namespace
    from .utils import is_kanji, to_hiragana
except ImportError:  # pytest / flat-import context
    from utils import is_kanji, to_hiragana

_MIN_PREFIX_LENGTH = 2
_MIN_SUFFIX_LENGTH = 2
_MIN_STEM_LENGTH = 2
_HONORIFIC_PREFIXES = ("お", "ご", "御")
_PHRASE_PARTICLES = frozenset("をがのにではもへと")
_SURU_SUFFIXES = ("する", "じる", "ずる")
# う段 -> い段, the godan 連用形 shift (遊ぶ -> 遊び). る is in here for the godan reading
# of a る-final verb; the ichidan reading (drop る) is generated alongside it.
_U_TO_I = {"う": "い", "く": "き", "ぐ": "ぎ", "す": "し", "つ": "ち",
           "ぬ": "に", "ぶ": "び", "む": "み", "る": "り"}
_ADJ_NOMINALIZERS = ("さ", "み", "げ")
# The い-adjective 連用形 (早い -> 早く), a stem candidate beside the nominalizers.
_ADJ_RENYOU = "く"
# う段 -> あ段, the godan 未然形 shift (拘わる -> 拘わら), which the negative suffixes attach to.
_U_TO_A = {"う": "わ", "く": "か", "ぐ": "が", "す": "さ", "つ": "た",
           "ぬ": "な", "ぶ": "ば", "む": "ま", "る": "ら"}
# ない is left out: つまらない, くだらない and 分からない would inflate their base verbs.
_NEGATIVE_SUFFIXES = ("ず", "ぬ")
# The godan て-form sound changes (急ぐ -> 急いで, 沿う -> 沿って). Ichidan just adds て.
_TE_ONBIN = {"う": "って", "つ": "って", "る": "って", "む": "んで", "ぶ": "んで", "ぬ": "んで",
             "く": "いて", "ぐ": "いで", "す": "して"}
# Every tail a _conjugated_tail_forms form can end in.
_CONJUGATED_TAILS = _NEGATIVE_SUFFIXES + ("て", "で")
# Rendaku: the voiced forms a compound's second element can take on its first kana
# (触り -> 手触り/てざわり). ち and つ have two voiced spellings each.
_RENDAKU = {
    "か": "が", "き": "ぎ", "く": "ぐ", "け": "げ", "こ": "ご",
    "さ": "ざ", "し": "じ", "す": "ず", "せ": "ぜ", "そ": "ぞ",
    "た": "だ", "ち": "ぢじ", "つ": "づず", "て": "で", "と": "ど",
    "は": "ば", "ひ": "び", "ふ": "ぶ", "へ": "べ", "ほ": "ぼ",
}

def _rendaku_forms(reading: str) -> Tuple[str, ...]:
    """``reading`` plus each form with its first kana voiced (さわり -> ざわり)."""
    if not reading:
        return (reading,)
    return (reading,) + tuple(v + reading[1:] for v in _RENDAKU.get(reading[0], ""))

def _is_phrase_entry(expression: str, reading: Optional[str]) -> bool:
    """Structural test for the single-kanji phrase rule: kanji head, whitelisted particle
    second, and a reading to validate against. The tail is OPTIONAL. A bare 'X<particle>'
    adverbial (俗に, 特に, 既に) is as much a use of X as 'X<particle><tail>' is."""
    return (
        bool(reading)
        and len(expression) >= 2
        and expression[1] in _PHRASE_PARTICLES
        and is_kanji(expression[0])
    )

def _is_suffix_phrase_entry(expression: str, reading: Optional[str]) -> bool:
    """Tail mirror of _is_phrase_entry: kanji tail, whitelisted particle right before it,
    non-empty head, and a reading to validate against (母の日/ははのひ credits 日/ひ)."""
    return (
        bool(reading)
        and len(expression) >= 3
        and expression[-2] in _PHRASE_PARTICLES
        and is_kanji(expression[-1])
    )

def _is_suru_entry(expression: str, reading: Optional[str]) -> bool:
    """Structural test for the single-kanji suru-verb rule: a reading to validate against and
    a written form that is exactly one kanji plus する/じる/ずる (屯する, 愛する, 感じる, 信ずる).

    The single-KANJI head is what prevents double-counting. A '㋕'-marked entry is re-keyed
    under its reading, so combine_word_forms + prefix_total(reading) already credits it.

    No tail and no infix. 重んじる is 重 + んじる, not 重 + じる, and never matches."""
    return (
        bool(reading)
        and len(expression) == 3
        and expression.endswith(_SURU_SUFFIXES)
        and is_kanji(expression[0])
    )

def _suru_reading_matches(card_reading: str, entry_reading: str, suffix: str) -> bool:
    """Reading validation for the suru rule: the entry must read as the card's reading plus
    the suffix, so a 屯/たむろ card takes 屯する/たむろする while a 屯/とん card does not.

    One tolerance: the regular sokuon change before する (察/さつ -> 察する/さっする), which
    carries an eighth of the rule. Gated to する because っじる/っずる do not occur, and to
    readings of 2+ morae so a bare つ cannot degenerate into a bare っする.

    Accepted imprecision: a matching reading is not proof of a matching sense (課/か <- 課する,
    上/うわ <- 上ずる), and じる is not always a suffix, so 恥じる (恥/はじ + る) misses the
    natural 恥/はじ card while crediting a 恥/は one (same for 閉じる, 混じる, 交じる)."""
    if entry_reading == card_reading + suffix:
        return True
    return (
        suffix == "する"
        and len(card_reading) >= 2
        and card_reading.endswith("つ")
        and entry_reading == card_reading[:-1] + "っする"
    )

def _stem_candidates(expression: str, reading: str) -> List[Tuple[str, str]]:
    """The (expression, reading) pairs a dictionary-form card should also be credited for: its
    連用形 (戒める -> 戒め, 遊ぶ -> 遊び, 早い -> 早く) and, for い-adjectives, its さ/み/げ
    nominalizations (強い -> 強さ, 痛い -> 痛み). Forward only. The card is the base form, the entry
    the derived one.

    The conjugation class is unknowable without a dictionary, so a る-final card yields BOTH the
    ichidan (drop る) and godan (る -> り) candidates and lets the exact (expression, reading)
    probe in stem_total arbitrate. Over 13 dictionaries, only 3 of 827 verb gains had both hit.

    Two gates carry the rule:

      * ``expression == reading`` is REJECTED. A candidate must match on expression AND reading.
        When the two are identical the probe degenerates to a kana lookup that validates nothing,
        and _build_index_from_raw re-keys every '㋕' entry under its reading, so the index is full
        of (kana, kana) pairs to hit. Drops 49% of the rule's raw credit (それる<-それ,
        わたす<-わたし) and zero legitimate matches. Also what keeps the rule stable under
        kana_normalization, which otherwise makes every ウ段-final loanword a verb candidate.
      * ``expression[-1] == reading[-1]``, the okurigana invariant: the edit is valid on both
        strings only when they end in the same kana, which is precisely when the tail IS
        okurigana. Rejects every kanji-final card (30,510 of 40,449 pairs measured).

    Candidates below _MIN_STEM_LENGTH are dropped: the bare-kanji noun blowups (神る->神, 太る->太)
    outweigh the correct single-kanji stems (見る->見, 出る->出) beside them.

    する is NOT handled. It is irregular, so 勉強する yields 勉強す rather than the correct 勉強し
    (no hits, so it costs nothing). じる/ずる verbs DO work (感じる -> 感じ), inflecting as ichidan.
    _is_suru_entry above does special-case all three."""
    if not expression or not reading or expression == reading:
        return []
    tail = expression[-1]
    if tail != reading[-1]:
        return []
    out: List[Tuple[str, str]] = []
    if tail in _U_TO_I:
        if tail == "る":
            out.append((expression[:-1], reading[:-1]))  # ichidan
        i = _U_TO_I[tail]
        out.append((expression[:-1] + i, reading[:-1] + i))  # godan
    elif tail == "い":
        for suffix in _ADJ_NOMINALIZERS + (_ADJ_RENYOU,):
            out.append((expression[:-1] + suffix, reading[:-1] + suffix))
    return [pair for pair in out if len(pair[0]) >= _MIN_STEM_LENGTH]

def _negative_forms(expression: str, reading: str) -> List[Tuple[str, str]]:
    """The (expression, reading) pairs of a verb card's 未然形 + ず/ぬ (拘わる -> 拘わらず, 思う ->
    思わぬ), the tails suffix matching credits besides the card itself.

    Same gates as _stem_candidates: ``expression == reading`` rejected, the okurigana invariant,
    and a う段 tail. A る-final card yields both the ichidan (drop る) and godan (る -> ら) forms,
    left for the reading check in conjugated_suffix_total to arbitrate.

    No _MIN_STEM_LENGTH floor. The suffix makes every form at least two characters (見る -> 見ず),
    and the bare-kanji blowups that floor exists for cannot happen once a suffix is attached.

    い-adjectives (少なからず), する (せず) and 来る (こず) are not handled."""
    if not expression or not reading or expression == reading:
        return []
    tail = expression[-1]
    if tail != reading[-1] or tail not in _U_TO_A:
        return []
    stems: List[Tuple[str, str]] = []
    if tail == "る":
        stems.append((expression[:-1], reading[:-1]))  # ichidan
    a = _U_TO_A[tail]
    stems.append((expression[:-1] + a, reading[:-1] + a))  # godan
    return [(stem_expr + suffix, stem_reading + suffix)
            for stem_expr, stem_reading in stems for suffix in _NEGATIVE_SUFFIXES]

def _te_forms(expression: str, reading: str) -> List[Tuple[str, str]]:
    """The (expression, reading) pairs of a verb card's て-form (急ぐ -> 急いで, 沿う -> 沿って,
    極める -> 極めて), which dictionaries list for the adverbs and set phrases built on it.

    Same gates as _negative_forms. A る-final card yields both the ichidan (drop る, add て) and
    godan (る -> って) forms. A く-final card read いく/ゆく also yields って, for 行く's irregular
    行って. する, 来る, and う-verbs keeping their う (問う -> 問うて) are not handled."""
    if not expression or not reading or expression == reading:
        return []
    tail = expression[-1]
    if tail != reading[-1] or tail not in _TE_ONBIN:
        return []
    out: List[Tuple[str, str]] = []
    if tail == "る":
        out.append((expression[:-1] + "て", reading[:-1] + "て"))  # ichidan
    endings = [_TE_ONBIN[tail]]
    if tail == "く" and reading.endswith(("いく", "ゆく")):
        endings.append("って")
    for ending in endings:
        out.append((expression[:-1] + ending, reading[:-1] + ending))
    return out

def _conjugated_tail_forms(expression: str, reading: str) -> List[Tuple[str, str]]:
    """Every conjugated form conjugated_suffix_total looks for at the end of an entry."""
    return _negative_forms(expression, reading) + _te_forms(expression, reading)

def _is_conjugated_tail_entry(expression: str, reading: Optional[str]) -> bool:
    """Structural test for the conjugated-form tail rule: a reading to validate against, a written
    form ending in ず/ぬ/て/で, and ``expression != reading``, which drops the (kana, kana) pairs
    _build_index_from_raw makes of '㋕' entries. Those can never end in a kanji-bearing form."""
    return (
        bool(reading)
        and expression.endswith(_CONJUGATED_TAILS)
        and expression != reading
    )

_VARIANTS_FILE = "kanji_variants.txt"
_glyph_canon_map: Optional[Dict[str, str]] = None

def _glyph_canon() -> Dict[str, str]:
    """kanji -> the canonical glyph of its KANJIDIC2 variant group (燈 -> 灯, 醬 -> 醤), from
    kanji_variants.txt. Read on first use, never at import. A missing or unreadable file
    degrades to no folding rather than breaking the reorder."""
    global _glyph_canon_map
    if _glyph_canon_map is not None:
        return _glyph_canon_map
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _VARIANTS_FILE)
    canon: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line[0] == "#":
                    continue
                for ch in line[1:]:
                    canon[ch] = line[0]
    except OSError as e:
        print("[priority-reorder] kanji variant table unavailable: %s" % e)
    _glyph_canon_map = canon
    return canon

def _kanji_skeleton(expression: str) -> str:
    """The deduplicated kanji of a written form, in first-appearance order (煌燦めく -> 煌燦,
    人々 -> 人 since 々 is not a kanji). Empty for kana-only forms, which is what gates those
    out of variant matching.

    Each kanji is folded to its glyph-variant group's canonical form first (燈す -> 灯), so a
    card and an entry that differ only in glyph (燈す/灯す, 掻く/搔く) nest. Every skeleton
    consumer, counting and seen alike, goes through here, so the fold cannot apply on one side
    and not the other."""
    canon = _glyph_canon()
    out: List[str] = []
    for ch in expression:
        if is_kanji(ch):
            ch = canon.get(ch, ch)
            if ch not in out:
                out.append(ch)
    return "".join(out)

def _variant_kanji_compatible(card_kanji: str, entry_kanji: str) -> bool:
    """The "like enough" test for variant matching: both skeletons non-empty AND one's kanji
    set nested inside the other's. Equal sets are the okurigana case (煌く/煌めく, 落葉/落ち葉).
    A strict superset is the added-kanji case (煌めく←煌燦めく).

    Nesting rather than mere intersection is the point. Same-reading homophone pairs usually DO
    share one kanji but never nest (科学/化学, 保証/保障, 開放/解放), so requiring nesting keeps
    genuinely different words apart."""
    if not card_kanji or not entry_kanji:
        return False
    a, b = set(card_kanji), set(entry_kanji)
    return a <= b or b <= a

def _is_variant_entry(expression: str, reading: Optional[str]) -> bool:
    """Structural test for the variant rule: the entry needs a reading to key on (variants are
    grouped by identical reading) and at least one kanji to share."""
    return bool(reading) and any(is_kanji(ch) for ch in expression)

def _honorific_fold_allowed(stripped: str, vocab) -> bool:
    """Whether an honorific-stripped remainder may be registered as a fold target. Allowed when
    the dict independently recognizes it, OR when it carries a kanji (お茶の間→茶の間, お金→金,
    near-certainly the same lexeme). Kana-only strips stay gated on dict membership, since that
    is where the unrelated-word junk lives (おかず→かず, おはよう→はよう)."""
    return bool(stripped) and (stripped in vocab or any(is_kanji(ch) for ch in stripped))

def _suffix_eligible(expression: str) -> bool:
    """Whether a card expression may receive *bare* suffix-matching credit: length >= 2 AND
    contains a kanji. That set is "real words" (学校, 目的, 食べる, 強い), whose reading and
    meaning carry across compounds. It excludes bare single kanji (日/手/語) and pure kana
    (する/こと, loanwords), which would match far too broadly.

    The tail mirror of _MIN_PREFIX_LENGTH. Single kanji return only via the reading-validated
    carve-out single_kanji_suffix_phrase_total (the head-side gate has two,
    single_kanji_phrase_total and single_kanji_suru_total)."""
    return len(expression) >= _MIN_SUFFIX_LENGTH and any(is_kanji(ch) for ch in expression)
_COMBINED_MEMO_CAP = 50_000

# Reserved child folder under user_files holding the daily seen dicts
# (user_files/_seen/<YYYY-MM-DD>/term_meta_bank_*.json). Owned by the `seen:N` search term
# (see seen_manager.py) and must never be treated as a normal occurrence dictionary, so it
# is excluded from dict enumeration, expansion and updating.
SEEN_FOLDER = "_seen"

class OccurrenceIndex:
    def __init__(self) -> None:
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}
        self.honorific_to_count: Dict[str, int] = {}
        # Every index below is built lazily by its own _ensure_* method, on the first query
        # that needs it.
        self._prefix_exprs: Optional[List[str]] = None
        self._prefix_cumsum: List[int] = []
        # Expressions sorted by their reversed form, with a parallel cumsum:
        # d ends with e  <=>  reverse(d) starts with reverse(e).
        self._suffix_revs: Optional[List[str]] = None
        self._suffix_cumsum: List[int] = []
        self._phrase_index: Optional[Dict[str, List[Tuple[str, str, int]]]] = None
        self._suffix_phrase_index: Optional[Dict[str, List[Tuple[str, str, int]]]] = None
        self._suru_index: Optional[Dict[str, List[Tuple[str, str, int]]]] = None
        # reading -> the kanji-bearing forms carrying it, as (expression, count). No skeleton
        # is cached; see _ensure_variant_index.
        self._variant_index: Optional[Dict[str, List[Tuple[str, int]]]] = None
        # The (expression, reading) keys sorted, with their expressions alongside for the bisect.
        self._stem_compound_keys: Optional[List[Tuple[str, str]]] = None
        self._stem_compound_exprs: List[str] = []
        # The ず/ぬ-final (expression, reading) keys sorted by reversed expression, with those
        # reversed expressions alongside for the bisect.
        self._conjugated_keys: Optional[List[Tuple[str, str]]] = None
        self._conjugated_revs: List[str] = []
        # Every (expression, reading) key whose two sides differ, sorted by reversed expression.
        self._stem_tail_keys: Optional[List[Tuple[str, str]]] = None
        self._stem_tail_revs: List[str] = []

    def add(self, expression: str, reading: Optional[str], count: int) -> None:
        if reading:
            key = (expression, reading)
            self.expr_reading_to_count[key] = self.expr_reading_to_count.get(key, 0) + count

        # Always fall back to expression alone, to account for reading mismatches.
        self.expr_to_count[expression] = self.expr_to_count.get(expression, 0) + count

    def _ensure_prefix_index(self) -> None:
        if self._prefix_exprs is not None:
            return
        items = sorted(self.expr_to_count.items())
        self._prefix_exprs = [expr for expr, _ in items]
        cumsum = [0]
        for _, count in items:
            cumsum.append(cumsum[-1] + count)
        self._prefix_cumsum = cumsum

    def prefix_total(self, expression: str) -> int:
        """Sum the counts of all terms that have ``expression`` as a *strict* prefix (longer
        terms only, since the exact match is credited by ``get``).

        Binary search over a lazily-built sorted index, so there is no per-term prefix
        explosion at build time."""
        if len(expression) < _MIN_PREFIX_LENGTH:
            return 0
        self._ensure_prefix_index()
        exprs = self._prefix_exprs
        # Sentinel must be U+10FFFF, the max code point. U+FFFF would sort before terms whose
        # next char is a supplementary-plane kanji like 𠮟, silently missing them.
        lo = bisect.bisect_left(exprs, expression)
        hi = bisect.bisect_left(exprs, expression + chr(0x10FFFF))
        if lo < len(exprs) and exprs[lo] == expression:
            lo += 1  # exclude the exact match (counted separately by get)
        return self._prefix_cumsum[hi] - self._prefix_cumsum[lo]

    def _ensure_suffix_index(self) -> None:
        if self._suffix_revs is not None:
            return
        # Reverse each expression ONCE into a keyed map, then sort the reversed strings
        # directly. A `key=lambda kv: kv[0][::-1]` over the forward keys reverses every
        # expression a second time, and sorting (reversed, count) tuples is slower still. The
        # reversal is injective, so no two entries collide.
        rev_to_count = {expr[::-1]: count for expr, count in self.expr_to_count.items()}
        revs = sorted(rev_to_count)
        cumsum = [0]
        for rev in revs:
            cumsum.append(cumsum[-1] + rev_to_count[rev])
        self._suffix_revs = revs
        self._suffix_cumsum = cumsum

    def suffix_total(self, expression: str) -> int:
        """Sum the counts of all terms that have ``expression`` as a *strict* written suffix
        (longer terms only, since the exact match is credited by ``get``), gated to kanji-bearing
        card expressions (see ``_suffix_eligible``).

        The suffix mirror of ``prefix_total``, over a lazily-built index of reversed
        expressions. Same O(log n) cost, no per-term suffix explosion at build time."""
        if not _suffix_eligible(expression):
            return 0
        self._ensure_suffix_index()
        revs = self._suffix_revs
        rev = expression[::-1]
        # Same U+10FFFF sentinel as prefix_total, over the reversed strings.
        lo = bisect.bisect_left(revs, rev)
        hi = bisect.bisect_left(revs, rev + chr(0x10FFFF))
        if lo < len(revs) and revs[lo] == rev:
            lo += 1  # exclude the exact match (counted separately by get)
        return self._suffix_cumsum[hi] - self._suffix_cumsum[lo]

    def _ensure_conjugated_index(self) -> None:
        """Sort the ず/ぬ/て/で-final pair keys by reversed expression. _is_conjugated_tail_entry keeps
        this to a sliver of the dict, so unlike the suffix index it can carry readings."""
        if self._conjugated_keys is not None:
            return
        rows = sorted(
            (expr[::-1], (expr, reading))
            for expr, reading in self.expr_reading_to_count
            if _is_conjugated_tail_entry(expr, reading)
        )
        self._conjugated_keys = [key for _rev, key in rows]
        self._conjugated_revs = [rev for rev, _key in rows]

    def conjugated_suffix_total(
        self,
        expression: str,
        reading: str,
        *,
        compound_matching: bool = False,
    ) -> int:
        """Sum the counts of entries ending in one of the card's conjugated forms (see
        _conjugated_tail_forms), on both sides. The 未然形 + ず/ぬ: にも拘わらず credits 拘わる,
        相変わらず credits 変わる, 見知らぬ credits 知る. The て-form: に沿って credits 沿う, この期に
        及んで credits 及ぶ. The conjugated-form half of suffix matching. suffix_total cannot see
        these, because the card's own final kana is gone.

        The reading tail is required, unlike suffix_total's written-only match. The forms are
        derived, and the derivation is where the false positives live: 水入らず (みずいらず) must
        not credit 入る/はいる, nor にも拘らず (にもかかわらず) 拘る/こだわる.

        The form itself is included (思わず credits 思う, 急いで credits 急ぐ), since get credits
        nothing for it.

        Gated like suffix_total (see _suffix_eligible), plus two skips:

          * Entries starting with the card expression, unconditionally. They belong to
            prefix_total, as in stem_compound_total.
          * ``compound_matching`` is a DEDUP GUARD. An entry starting with one of the card's
            _stem_candidates on both sides was already credited by stem_compound_total. For a
            る-final card that is the form itself: 拘わらず sits in the 拘わ/かかわ sweep, and an
            ichidan て-form (改めて) in the 改め/あらため one.

        Nothing else overlaps. suffix_total's entries end in the card's う段 kana, not
        ず/ぬ/て/で. variant_total needs the card's exact reading. stem_total probes a candidate
        exactly and stem_tail_compound_total wants entries ending in one, and no candidate is a
        conjugated form: an ichidan stem can end in て (捨て), but its form then ends in てて.
        honorific_to_count is keyed on the bare card, and the single-kanji carve-outs are gated
        out with it.

        Reads only expr_reading_to_count, so sum_d conjugated_suffix_total_d == the merged total."""
        if not _suffix_eligible(expression):
            return 0
        forms = _conjugated_tail_forms(expression, reading)
        if not forms:
            return 0
        self._ensure_conjugated_index()
        keys = self._conjugated_keys
        revs = self._conjugated_revs
        pair_counts = self.expr_reading_to_count
        compound_stems = _stem_candidates(expression, reading) if compound_matching else ()
        credited = set()
        total = 0
        for form_expr, form_reading in forms:
            rev = form_expr[::-1]
            # Same U+10FFFF sentinel as prefix_total.
            lo = bisect.bisect_left(revs, rev)
            hi = bisect.bisect_left(revs, rev + chr(0x10FFFF))
            for position in range(lo, hi):
                key = keys[position]
                if key in credited:
                    continue
                entry_expr, entry_reading = key
                if not entry_reading.endswith(form_reading):
                    continue
                if entry_expr.startswith(expression):
                    continue
                if any(entry_expr.startswith(stem_expr) and entry_reading.startswith(stem_reading)
                       for stem_expr, stem_reading in compound_stems):
                    continue
                credited.add(key)
                total += pair_counts[key]
        return total

    def _ensure_phrase_index(self) -> None:
        if self._phrase_index is not None:
            return
        index: Dict[str, List[Tuple[str, str, int]]] = {}
        for (expr, reading), count in self.expr_reading_to_count.items():
            if not _is_phrase_entry(expr, reading):
                continue
            index.setdefault(expr[0], []).append((expr[1], reading, count))
        self._phrase_index = index

    def single_kanji_phrase_total(self, expression: str, reading: str) -> int:
        """Phrase credit for a single-kanji card: sums entries 'X<particle>' with an optional
        tail whose reading starts with the card's reading + the particle, validating that X is
        read in-context as the card reads it (手を貸す/てをかす and 俗に/ぞくに credit 手/て and
        俗/ぞく, but 手/しゅ gets nothing).

        One of the two carve-outs complementing prefix_total, which gates out single-character
        expressions entirely. The other is single_kanji_suru_total."""
        if len(expression) != 1 or not reading or not is_kanji(expression):
            return 0
        self._ensure_phrase_index()
        total = 0
        for particle, entry_reading, count in self._phrase_index.get(expression, ()):
            if entry_reading.startswith(reading + particle):
                total += count
        return total

    def _ensure_suffix_phrase_index(self) -> None:
        if self._suffix_phrase_index is not None:
            return
        index: Dict[str, List[Tuple[str, str, int]]] = {}
        for (expr, reading), count in self.expr_reading_to_count.items():
            if not _is_suffix_phrase_entry(expr, reading):
                continue
            index.setdefault(expr[-1], []).append((expr[-2], reading, count))  # bucket by LAST char
        self._suffix_phrase_index = index

    def single_kanji_suffix_phrase_total(self, expression: str, reading: str) -> int:
        """Tail mirror of single_kanji_phrase_total: credit from entries '<head><particle>X'
        whose reading ends with the particle + the card's reading (母の日/ははのひ credits 日/ひ
        but not 日/にち). An EXACT match, because particles sit on a word boundary, so the tail
        kanji never rendakus. Complements suffix_total, which gates out single-character
        expressions entirely."""
        if len(expression) != 1 or not reading or not is_kanji(expression):
            return 0
        self._ensure_suffix_phrase_index()
        total = 0
        for particle, entry_reading, count in self._suffix_phrase_index.get(expression, ()):
            if entry_reading.endswith(particle + reading):
                total += count
        return total

    def _ensure_suru_index(self) -> None:
        if self._suru_index is not None:
            return
        index: Dict[str, List[Tuple[str, str, int]]] = {}
        for (expr, reading), count in self.expr_reading_to_count.items():
            if not _is_suru_entry(expr, reading):
                continue
            index.setdefault(expr[0], []).append((expr[1:], reading, count))  # bucket by head kanji
        self._suru_index = index

    def single_kanji_suru_total(self, expression: str, reading: str) -> int:
        """Suru-verb credit for a single-kanji card: sums entries 'X<する|じる|ずる>' whose reading
        is the card's reading plus that suffix (屯/たむろ takes 屯する/たむろする, 感/かん takes
        感じる/かんじる, but 屯/とん takes nothing).

        The third complement to prefix_total. Nothing double-counts these: す/じ/ず are not phrase
        particles so single_kanji_phrase_total cannot reach them, the entry ends in る (not in X)
        so the suffix rules miss it, and its reading is strictly longer than the card's, so
        variant_total, which buckets on an IDENTICAL reading, cannot see it."""
        if len(expression) != 1 or not reading or not is_kanji(expression):
            return 0
        self._ensure_suru_index()
        total = 0
        for suffix, entry_reading, count in self._suru_index.get(expression, ()):
            if _suru_reading_matches(reading, entry_reading, suffix):
                total += count
        return total

    def _ensure_variant_index(self) -> None:
        """Bucket the (expression, reading) pairs by reading, keeping only kanji-bearing entries.

        Stores NO kanji skeleton: a bucket averages under two forms, so recomputing the handful a
        query touches beats skeletonizing every entry up front (28% off the build and 35% off the
        retained memory across 10 dictionaries).

        Do not relax ``_is_variant_entry``. Keeping kana-only entries out of the index is what
        stops a kana dict entry crediting a kanji card. That bridge belongs to combine_word_forms."""
        if self._variant_index is not None:
            return
        index: Dict[str, List[Tuple[str, int]]] = {}
        for (expr, reading), count in self.expr_reading_to_count.items():
            if not _is_variant_entry(expr, reading):
                continue
            index.setdefault(reading, []).append((expr, count))
        self._variant_index = index

    def variant_total(
        self,
        expression: str,
        reading: str,
        *,
        card_kanji: Optional[str] = None,
        prefix_matching: bool = False,
        suffix_matching: bool = False,
    ) -> int:
        """Sum the counts of *other written forms of the same word*: entries with the identical
        reading whose kanji nest with the card's (see _variant_kanji_compatible). The one rule
        that bridges okurigana and alternate-spelling differences, which the written prefix/suffix
        indexes structurally cannot see, since 煌く is neither a prefix nor a suffix of 煌めく.
        Kana-only forms have an empty skeleton and never participate on either side.

        Grouping by reading keeps the per-query candidate list tiny, so this is a dict lookup plus
        a few short-string comparisons.

        ``card_kanji`` is an optional precomputed ``_kanji_skeleton(expression)`` for the
        multi-dict path, where CombinedOccurrenceIndex would otherwise recompute it once per
        dictionary. It cannot change the result. Left None, it is derived here.

        ``prefix_matching``/``suffix_matching`` are DEDUP GUARDS, not widening knobs. With those
        flags on, a variant that is also a strict written prefix/suffix of the card expression was
        already summed by prefix_total/suffix_total (card 気持/きもち + entry 気持ち/きもち), so it
        is skipped rather than counted twice."""
        if card_kanji is None:
            card_kanji = _kanji_skeleton(expression)
        if not card_kanji or not reading:
            return 0
        self._ensure_variant_index()
        prefix_dedup = prefix_matching and len(expression) >= _MIN_PREFIX_LENGTH
        suffix_dedup = suffix_matching and _suffix_eligible(expression)
        total = 0
        for entry_expr, count in self._variant_index.get(reading, ()):
            if entry_expr == expression:
                continue  # the card's own form, credited by get()
            # Cheap string guards first: a candidate already credited by prefix_total/suffix_total
            # never has to pay for a skeleton computation plus the nesting test.
            if prefix_dedup and entry_expr.startswith(expression):
                continue  # already in prefix_total(expression)
            if suffix_dedup and entry_expr.endswith(expression):
                continue  # already in suffix_total(expression)
            if not _variant_kanji_compatible(card_kanji, _kanji_skeleton(entry_expr)):
                continue
            total += count
        return total

    def stem_total(
        self,
        expression: str,
        reading: str,
        *,
        combine_word_forms: bool = False,
    ) -> int:
        """Sum the counts of the card's 連用形 and さ/み/げ forms, bridging a dictionary-form card
        to the derived form an occurrence dict lists separately (戒める takes 戒め, 強い takes 強さ,
        早い takes 早く).
        See _stem_candidates for the candidates and its two gates.

        Forward only. The reverse (a 連用形 card taking its dictionary form) was measured and
        rejected: a rare derived form would inherit the count of a far commoner base (無げ, base 1,
        would take 無い's 8484), inverting the priority ordering this addon exists to produce.
        Forward transfers large counts only when the derived form genuinely IS the commoner form of
        the same word (匂う <- 匂い). prefix_matching still covers the ichidan half of the reverse.

        Probes ``expr_reading_to_count`` DIRECTLY and must never route through ``self.get``, whose
        per-dict reading-mismatch fallback is the one non-linear term in the index. Routing through
        it passes every single-dict test while silently breaking the merged-vs-per-dict drift guard
        for combinators only. Both maps read here are plain per-dict sums, which is what keeps
        sum_d stem_total_d == stem_total_merged.

        No dedup guard, the only rule here needing none. Every candidate either shortens the card
        (ichidan) or replaces its final character (godan, adjective), so prefix_total needs strictly
        longer terms, suffix_total needs terms ending with the whole expression, variant_total
        buckets on the card's exact reading while every candidate reading differs, honorific_to_count
        is keyed a character longer, and get is keyed on the card itself. The single-kanji carve-outs
        are unreachable: a 1-character card either fails the okurigana invariant or yields a
        candidate below _MIN_STEM_LENGTH."""
        candidates = _stem_candidates(expression, reading)
        if not candidates:
            return 0
        pair_counts = self.expr_reading_to_count
        total = 0
        for cand_expr, cand_reading in candidates:
            total += pair_counts.get((cand_expr, cand_reading), 0)
        if combine_word_forms:
            # Mirror of get_total's combine_word_forms term. A kana-only entry is keyed under its
            # reading, so the stem's kana form is credited from expr_to_count. _stem_candidates
            # rejects expression == reading, so the two terms can never be one entry counted twice.
            for _cand_expr, cand_reading in candidates:
                total += self.expr_to_count.get(cand_reading, 0)
        return total

    def _ensure_stem_compound_index(self) -> None:
        """Sort the (expression, reading) keys, keeping the expressions in a parallel list.

        Two lists of pointers, not a materialized (expr, reading, count) row per entry: the keys
        are the dict's own tuples and the expressions its own strings, so the view costs the two
        lists and nothing else. On a merged index, an order of magnitude larger than a single
        dict, that difference is the whole memory cost of the rule."""
        if self._stem_compound_keys is not None:
            return
        keys = sorted(self.expr_reading_to_count)
        self._stem_compound_keys = keys
        self._stem_compound_exprs = [key[0] for key in keys]

    def stem_compound_total(
        self,
        expression: str,
        reading: str,
        *,
        card_kanji: Optional[str] = None,
        stem_matching: bool = False,
        variant_matching: bool = False,
    ) -> int:
        """Sum the counts of entries that COMPOUND on the card's stem: same 連用形 and さ/み/げ
        candidates as stem_total, but taken as a prefix rather than probed exactly, so a 奮う card
        reaches 奮い立つ and a 取る card reaches 取り消す. Occurrence dicts list those as their own
        entries, and no other rule can see them: 奮い立つ neither starts nor ends with 奮う, and
        its reading is not the card's, so prefix, suffix and variant matching all miss it.

        Both sides must match. The entry's expression starts with the candidate expression AND its
        reading with the candidate reading, which is what keeps a 抱く/いだく card off 抱きしめる
        while the 抱く/だく card takes it.

        Four gates beyond that, none of them optional:

          * Entries that start with the CARD expression are skipped unconditionally, not just
            under prefix_matching. They belong to prefix_total (and the card itself to get), and
            crediting them here would make this rule a silent superset of prefix matching for
            る-final cards, whose ichidan candidate 食べ swallows every 食べる… entry. Only that
            candidate can reach them: the godan and adjective candidates REPLACE the final
            character, so their range and the card's cannot overlap.
          * ``entry_expr == entry_reading`` is skipped. _build_index_from_raw re-keys every '㋕'
            entry under its reading, so the pair map is full of (kana, kana) entries that validate
            nothing. They are combine_word_forms' bridge, not this rule's.
          * ``stem_matching`` is a DEDUP GUARD, in the sense variant_total uses the term, not a
            widening knob. It skips the one entry stem_total already credits, and only when that
            rule is running: with stem_matching off this rule covers the exact stem itself, so it
            is a superset of stem_total's expression-side term rather than a hole beside it. The
            test is on the PAIR, since an entry spelled like the stem but read longer
            (戒め/いましめる) is invisible to stem_total's exact probe and belongs here.
          * ``variant_matching`` is the second dedup guard. The ichidan candidate reading is the
            card's reading minus its last kana, so an entry carrying the card's exact reading can
            sit inside the swept range and be credited by variant_total too. Okurigana variants
            are where this bites (立ち止る card, 立ち止まる entry).

        Candidates are swept shortest first, and one whose expression AND reading both extend an
        already-swept candidate is skipped: for a る-final card the godan range (食べり/たべり)
        nests inside the ichidan one (食べ/たべ), so its matches would be counted twice. The check
        has to be dynamic rather than 'sweep the ichidan one', because _MIN_STEM_LENGTH can drop
        the ichidan candidate (見る -> 見) and leave the godan sweep to run alone. The four
        adjective candidates (さ/み/げ/く) are pairwise disjoint.

        Nothing else double-counts. suffix_total would need an entry both beginning with the stem
        and ending with the whole base form; honorific_to_count sums 'お|ご|御 + expression'
        entries, which begin with the honorific; the single-kanji carve-outs are unreachable,
        since a 1-character card either fails _stem_candidates' okurigana invariant or yields a
        candidate below _MIN_STEM_LENGTH.

        Reads only expr_reading_to_count, a plain per-dict sum, and never routes through
        ``self.get``, so sum_d stem_compound_total_d == stem_compound_total_merged."""
        candidates = _stem_candidates(expression, reading)
        if not candidates:
            return 0
        self._ensure_stem_compound_index()
        keys = self._stem_compound_keys
        exprs = self._stem_compound_exprs
        pair_counts = self.expr_reading_to_count
        if card_kanji is None and variant_matching:
            card_kanji = _kanji_skeleton(expression)
        total = 0
        swept: List[Tuple[str, str]] = []
        for cand_expr, cand_reading in sorted(candidates, key=lambda pair: len(pair[0])):
            if any(cand_expr.startswith(expr) and cand_reading.startswith(read)
                   for expr, read in swept):
                continue
            swept.append((cand_expr, cand_reading))
            # Same U+10FFFF sentinel as prefix_total: U+FFFF would sort before terms whose next
            # character is a supplementary-plane kanji.
            lo = bisect.bisect_left(exprs, cand_expr)
            hi = bisect.bisect_left(exprs, cand_expr + chr(0x10FFFF))
            for position in range(lo, hi):
                key = keys[position]
                entry_expr, entry_reading = key
                if entry_expr.startswith(expression) or entry_expr == entry_reading:
                    continue
                if not entry_reading.startswith(cand_reading):
                    continue
                if stem_matching and entry_expr == cand_expr and entry_reading == cand_reading:
                    continue
                if (variant_matching and entry_reading == reading and card_kanji
                        and _variant_kanji_compatible(card_kanji, _kanji_skeleton(entry_expr))):
                    continue
                total += pair_counts[key]
        return total

    def _ensure_stem_tail_index(self) -> None:
        """Sort the pair keys by reversed expression, skipping the (kana, kana) pairs '㋕' entries
        are re-keyed as, which stem_compound_total throws out for the same reason."""
        if self._stem_tail_keys is not None:
            return
        rows = sorted(
            (expr[::-1], (expr, reading))
            for expr, reading in self.expr_reading_to_count
            if expr != reading
        )
        self._stem_tail_keys = [key for _rev, key in rows]
        self._stem_tail_revs = [rev for rev, _key in rows]

    def stem_tail_compound_total(self, expression: str, reading: str) -> int:
        """Sum the counts of entries that END in the card's stem: the tail mirror of
        stem_compound_total, so 稼ぐ takes 時間稼ぎ, 止まる takes 行き止まり and 惑う takes 戸惑い.
        Japanese compounds put the head last, and a 連用形 head neither starts nor ends with the
        card, so no other rule sees these.

        Both sides must match, the reading allowing rendaku on the stem's first kana when that
        kana is written as a kanji: 手触り reads てざわり, not てさわり. The entry must be strictly
        longer than the stem on both sides.

        Two skips:

          * Entries starting with the card expression. They belong to prefix_total, as in
            stem_compound_total.
          * Entries starting with one of the card's stem candidates on both sides. Those are
            stem_compound_total's, which always runs beside this rule (both sit under
            compound_matching).

        Nothing else overlaps. suffix_total's entries end in the whole base form, and a stem never
        does: it drops or replaces the final kana. variant_total needs the card's exact reading,
        which no entry ending in a stem reading can carry. stem_total probes the stem exactly, and
        this rule wants strictly longer entries. honorific_to_count keys the stripped form (お願い
        credits 願い), not the verb.

        A per-card ``credited`` set guards the candidates against each other, though none is
        known to be a suffix of another. Reads only expr_reading_to_count, so
        sum_d stem_tail_compound_total_d == the merged total."""
        candidates = _stem_candidates(expression, reading)
        if not candidates:
            return 0
        self._ensure_stem_tail_index()
        keys = self._stem_tail_keys
        revs = self._stem_tail_revs
        pair_counts = self.expr_reading_to_count
        credited = set()
        total = 0
        for cand_expr, cand_reading in candidates:
            rev = cand_expr[::-1]
            # A kana-initial stem shows its first kana in writing, so only a kanji can voice.
            tails = _rendaku_forms(cand_reading) if is_kanji(cand_expr[0]) else (cand_reading,)
            # Same U+10FFFF sentinel as prefix_total.
            lo = bisect.bisect_left(revs, rev)
            hi = bisect.bisect_left(revs, rev + chr(0x10FFFF))
            for position in range(lo, hi):
                key = keys[position]
                if key in credited:
                    continue
                entry_expr, entry_reading = key
                if len(entry_expr) == len(cand_expr) or entry_expr.startswith(expression):
                    continue
                if not any(len(entry_reading) > len(tail) and entry_reading.endswith(tail)
                           for tail in tails):
                    continue
                if any(entry_expr.startswith(stem_expr) and entry_reading.startswith(stem_reading)
                       for stem_expr, stem_reading in candidates):
                    continue
                credited.add(key)
                total += pair_counts[key]
        return total

    def get(self, expression: str, reading: str) -> int:
        if (expression, reading) in self.expr_reading_to_count:
            return self.expr_reading_to_count[(expression, reading)]
        return self.expr_to_count.get(expression, 0)

    def get_total(
        self,
        expression: str,
        reading: str,
        *,
        combine_word_forms: bool = False,
        prefix_matching: bool = False,
        suffix_matching: bool = False,
        variant_matching: bool = False,
        stem_matching: bool = False,
        compound_matching: bool = False,
        honorific_folding: bool = False,
        card_kanji: Optional[str] = None,
    ) -> int:
        """``card_kanji`` is forwarded to variant_total and stem_compound_total as a precomputed
        skeleton. An optimization hint only, and it must match what those would derive from
        ``expression``."""
        total = self.get(expression, reading)
        reading_is_distinct = bool(reading) and reading != expression
        if combine_word_forms and reading_is_distinct:
            total += self.expr_to_count.get(reading, 0)
        if prefix_matching:
            total += self.prefix_total(expression)
            if combine_word_forms and reading_is_distinct:
                total += self.prefix_total(reading)
            total += self.single_kanji_phrase_total(expression, reading)
            total += self.single_kanji_suru_total(expression, reading)
        if suffix_matching:
            # No reading-side term, unlike prefix. A kana reading is never suffix-eligible
            # (contains no kanji), so suffix_total(reading) is a definitional no-op.
            total += self.suffix_total(expression)
            # Single-kanji cards, gated out of the bare path, return only via the tail phrase
            # carve-out.
            total += self.single_kanji_suffix_phrase_total(expression, reading)
            total += self.conjugated_suffix_total(
                expression, reading, compound_matching=compound_matching
            )
        if variant_matching:
            # No reading-side term. A kana reading has an empty kanji skeleton, so
            # variant_total(reading) is a definitional no-op. The prefix/suffix flags are passed
            # only so credit already taken by prefix_total/suffix_total is skipped.
            total += self.variant_total(
                expression,
                reading,
                card_kanji=card_kanji,
                prefix_matching=prefix_matching,
                suffix_matching=suffix_matching,
            )
        if stem_matching:
            # No reading-side term beyond the one inside stem_total. A kana reading is identical
            # to its own stem candidates' readings, and _stem_candidates rejects the
            # expression == reading case outright, so stem_total(reading, reading) is a no-op.
            total += self.stem_total(
                expression, reading, combine_word_forms=combine_word_forms
            )
        if compound_matching:
            # No reading-side term and no combine_word_forms term. Both would mean sweeping the
            # kana stem, and the kana-keyed entries a sweep would find are exactly the ones the
            # rule's second gate throws out.
            total += self.stem_compound_total(
                expression,
                reading,
                card_kanji=card_kanji,
                stem_matching=stem_matching,
                variant_matching=variant_matching,
            )
            total += self.stem_tail_compound_total(expression, reading)
        if honorific_folding:
            # honorific_to_count credits the bare form from an 'お/ご/御 + form' entry, which is a
            # strict written suffix of that entry. So when the expression is suffix-eligible,
            # suffix_total(expression) already counted it, and the expression-side term is skipped
            # to avoid double-counting. Kana-only folds (かず←おかず) are not suffix-eligible, are
            # never subsumed, and keep their credit. The reading side is kana-tailed likewise.
            if not (suffix_matching and _suffix_eligible(expression)):
                total += self.honorific_to_count.get(expression, 0)
            if combine_word_forms and reading_is_distinct:
                total += self.honorific_to_count.get(reading, 0)
        return total

class CombinedOccurrenceIndex(OccurrenceIndex):
    """One MERGED index over several dictionaries, not a loop over them.

    Summing ``get_total`` across N per-dict indexes costs N times as much per card and
    materializes N sets of lazy views. Folding up front makes every lookup O(1) in the
    dictionary count (33.5 -> 9.6 us/card at 4 dicts, flat as dicts are added).

    The fold is EXACTLY equivalent to the per-dict sum, not an approximation:

      * ``expr_to_count`` / ``expr_reading_to_count`` are plain sums, and every rule reading
        them sums linearly over entries whose gates are structural tests on the query or on
        one entry's own strings, never on a dictionary's contents.
      * ``honorific_to_count`` is NOT rebuilt from the merged vocabulary. Its
        ``_honorific_fold_allowed`` gate consults the dictionary's own expr_to_count, and the
        merged vocabulary is a superset that would admit folds no single dict allowed. Each
        dict's map is built under its own gate, then summed.
      * ``get`` is the one non-linear term, because of its per-dict reading-mismatch fallback.
        That cannot collapse into either merged map, so ``_base_total`` precomputes it per pair.

    A drift-guard test pins merged totals against the per-dict sum across every flag combination.

    Only the two BUILD-time flags are constructor state. combine/prefix/suffix/variant/stem/
    compound are query-time flags that neither the fold nor any lazy view reads, so an index built under one
    combination is byte-identical to one built under another. Keeping them in the cache key left
    up to 64 identical merged copies of every dictionary resident after a few config flips, and a
    merged copy is an order of magnitude larger than a single-dict index."""

    def __init__(self, dict_names: List[str], normalize_kana: bool = False, honorific_folding: bool = False) -> None:
        super().__init__()
        self.dict_names = sorted(dict_names)
        self.normalize_kana = normalize_kana
        self.honorific_folding = honorific_folding
        # (expression, reading) -> what the per-dict `get` fallback would have summed to.
        # Filled by _fold; a pair absent from every dict falls back to expr_to_count.
        self._base_total: Dict[Tuple[str, str], int] = {}
        self._folded = False
        # Per-card memo of finished totals. MUST stay separate from expr_reading_to_count,
        # which holds real dictionary data the FIFO eviction below would silently delete and
        # the fold could never recompute. Totals depend on the query-time flags, fixed for a
        # run but not for the object's lifetime, so the memo is dropped when they change
        # rather than widening every key.
        self._memo: Dict[Tuple[str, str], int] = {}
        self._memo_flags: Optional[Tuple[bool, bool, bool, bool, bool, bool]] = None

    def _fold(self) -> None:
        """Merge every dictionary into this index. Lazy, so a combined index that is never
        queried (a search whose standard part matched nothing) never pays for it."""
        if self._folded:
            return
        self._folded = True

        expr_to_count = self.expr_to_count
        expr_reading_to_count = self.expr_reading_to_count
        honorific_to_count = self.honorific_to_count
        base_total = self._base_total

        # Folded from the SHARED cache rather than a private parse. Dictionaries recur across
        # combinators (`occurrences:[A,B]` and `occurrences:[A,C]`), and reparsing A for each
        # would cost more than the fold saves.
        indexes = [get_occurrence_index(name, self.normalize_kana, self.honorific_folding)
                   for name in self.dict_names]

        for index in indexes:
            for expr, count in index.expr_to_count.items():
                expr_to_count[expr] = expr_to_count.get(expr, 0) + count
            for key, count in index.expr_reading_to_count.items():
                expr_reading_to_count[key] = expr_reading_to_count.get(key, 0) + count
            for expr, count in index.honorific_to_count.items():
                honorific_to_count[expr] = honorific_to_count.get(expr, 0) + count

        # sum_d (pair_d[key] if present else expr_d[expr])
        #   == merged_expr[expr] + sum over the dicts that DO have the pair of
        #      (pair_d[key] - expr_d[expr])
        # The right-hand form visits each dict's own pairs once instead of probing every
        # dict for every key in the union, so the fold doesn't reintroduce a per-dict factor.
        for index in indexes:
            index_expr_to_count = index.expr_to_count
            for key, count in index.expr_reading_to_count.items():
                expr = key[0]
                if key not in base_total:
                    base_total[key] = expr_to_count.get(expr, 0)
                base_total[key] += count - index_expr_to_count.get(expr, 0)

    def get(self, expression: str, reading: str) -> int:
        """The merged BASE count, i.e. the per-dict `get` fallback summed. Overrides
        OccurrenceIndex.get, which the inherited get_total calls first."""
        if not self._folded:
            self._fold()
        value = self._base_total.get((expression, reading))
        if value is not None:
            return value
        return self.expr_to_count.get(expression, 0)

    def total(
        self,
        expression: str,
        reading: str,
        card_kanji: Optional[str] = None,
        *,
        combine_word_forms: bool = False,
        prefix_matching: bool = False,
        suffix_matching: bool = False,
        variant_matching: bool = False,
        stem_matching: bool = False,
        compound_matching: bool = False,
    ) -> int:
        """Flag-inclusive total across every dict for one card, memoized per card. The entry
        point ``occurrence_count`` and ``occurrence_counter`` use.

        PRECONDITION: ``expression`` and ``reading`` must already be kana-folded when
        ``normalize_kana`` is set. The callers do that unconditionally before reaching either
        path, and the indexes are keyed on folded strings.

        The six query-time flags are arguments rather than constructor state so the merged
        index can be shared across flag combinations (see the class docstring).
        ``honorific_folding`` stays on the instance because it is a build flag, and
        ``honorific_to_count`` is only populated when the per-dict indexes were built with it.

        ``card_kanji`` is an optional precomputed skeleton. See ``variant_total``."""
        flags = (combine_word_forms, prefix_matching, suffix_matching, variant_matching,
                 stem_matching, compound_matching)
        memo = self._memo
        if flags != self._memo_flags:
            # Run-fixed in practice; this only fires when a config flag is flipped between
            # reorders, or in the drift-guard tests that sweep every combination.
            memo.clear()
            self._memo_flags = flags

        key = (expression, reading)
        cached = memo.get(key)
        if cached is not None:
            return cached

        if not self._folded:
            self._fold()

        # Derived after the memo check so repeat lookups don't pay for it, and skipped when
        # the caller already has it. to_hiragana leaves CJK ideographs untouched, so this is
        # identical whether or not the caller folded (see the precondition above).
        if card_kanji is None and (variant_matching or compound_matching):
            card_kanji = _kanji_skeleton(expression)

        total_count = self.get_total(
            expression,
            reading,
            combine_word_forms=combine_word_forms,
            prefix_matching=prefix_matching,
            suffix_matching=suffix_matching,
            variant_matching=variant_matching,
            stem_matching=stem_matching,
            compound_matching=compound_matching,
            honorific_folding=self.honorific_folding,
            card_kanji=card_kanji,
        )

        # Bounded FIFO eviction (dicts preserve insertion order) instead of
        # clearing the whole memo, which would thrash when the working set
        # exceeds the cap.
        if len(memo) >= _COMBINED_MEMO_CAP:
            memo.pop(next(iter(memo)))
        memo[key] = total_count
        return total_count

def _dict_dir(dict_name: str) -> str:
    return os.path.join(os.path.dirname(__file__), "user_files", dict_name)

def _load_index_file(dict_dir: str) -> Optional[str]:
    if not os.path.isdir(dict_dir):
        return None
    for name in os.listdir(dict_dir):
        if name.startswith("term_meta_bank_") and name.endswith(".json"):
            return os.path.join(dict_dir, name)
    return None

def get_all_dict_names() -> List[str]:
    """Returns a sorted list of all dictionary names in user_files, ignoring 'all',
    the reserved '_seen' folder, and dot-prefixed temp dirs (updater swap leftovers)."""
    user_files_dir = os.path.join(os.path.dirname(__file__), "user_files")
    if not os.path.isdir(user_files_dir):
        return []

    dict_names = []
    for item in os.listdir(user_files_dir):
        if item == "all" or item == SEEN_FOLDER or item.startswith("."):
            continue
        item_path = os.path.join(user_files_dir, item)
        if os.path.isdir(item_path):
            dict_names.append(item)
    return sorted(dict_names)

def _load_term_meta_raw(dict_name: str) -> Optional[list]:
    """Parse the dictionary's term meta bank from disk. Deliberately NOT cached. The raw list
    is huge (every entry of a 100k+ term bank) and only needed while building an
    OccurrenceIndex, and get_occurrence_index memoizes the compact result, so each dict is
    parsed once per session and the raw list is collected right after the build."""
    dir_path = _dict_dir(dict_name)
    index_path = _load_index_file(dir_path)
    if not index_path:
        return None
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        import traceback
        print(f"[priority-reorder] Failed to load {index_path}: {e}")
        traceback.print_exc()
        return None

def _build_index_from_raw(data: list, normalize_kana: bool = False, honorific_folding: bool = False) -> OccurrenceIndex:
    index = OccurrenceIndex()
    for entry in data:
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        expression = entry[0]
        meta = entry[2]
        reading = None
        count = 0
        is_kana_occurrences = False

        if isinstance(meta, dict):
            reading = meta.get("reading") if isinstance(meta.get("reading"), str) else None

            # '㋕' in a display value marks the entry as kana-only.
            display_val = str(meta.get("displayValue", ""))
            freq_obj = meta.get("frequency")
            if isinstance(freq_obj, dict):
                display_val += str(freq_obj.get("displayValue", ""))
                if isinstance(freq_obj.get("value"), int):
                    count = int(freq_obj["value"])

            if "㋕" in display_val:
                is_kana_occurrences = True

            if count == 0 and isinstance(meta.get("value"), int):
                count = int(meta["value"])
        elif isinstance(meta, int):
            count = meta
        elif isinstance(meta, str):
            try:
                count = int(meta)
            except ValueError:
                pass

        if isinstance(expression, str) and count > 0:
            # Kana-only entries are attributed to the reading, so crediting a kanji-bearing
            # card from one requires combine_word_forms at lookup time.
            effective_expression = reading if (is_kana_occurrences and reading) else expression
            if normalize_kana:
                effective_expression = to_hiragana(effective_expression)
                if reading:
                    reading = to_hiragana(reading)
            index.add(effective_expression, reading, count)

    if honorific_folding:
        for expr, count in list(index.expr_to_count.items()):
            if not expr.startswith(_HONORIFIC_PREFIXES):
                continue
            # Every entry in _HONORIFIC_PREFIXES is a single character.
            stripped = expr[1:]
            if not _honorific_fold_allowed(stripped, index.expr_to_count):
                continue
            index.honorific_to_count[stripped] = index.honorific_to_count.get(stripped, 0) + count

    return index

@lru_cache(maxsize=64)
def get_occurrence_index(dict_name: str, normalize_kana: bool = False, honorific_folding: bool = False) -> OccurrenceIndex:
    """Parsed index for one dictionary, memoized for the session.

    Only the two BUILD-time flags key this cache. prefix/suffix/variant/stem/compound matching
    are query-time flags whose indexes are lazy views derived from expr_to_count and
    expr_reading_to_count (stem matching has no view at all; compound matching sorts the pair
    keys), so an index built with them off is identical to one built with them on. Keying on
    them left up to 32 byte-identical copies
    of the same dictionary resident after a few flag flips (8.1 MB per dict with every view
    built)."""
    data = _load_term_meta_raw(dict_name)
    if data is None:
        return OccurrenceIndex()
    return _build_index_from_raw(data, normalize_kana, honorific_folding)

@lru_cache(maxsize=4)
def get_combined_occurrence_index(dict_names_tuple: Tuple[str, ...], normalize_kana: bool = False, honorific_folding: bool = False) -> CombinedOccurrenceIndex:
    """Merged index for one combinator, memoized for the session.

    Keyed on the same two BUILD-time flags as ``get_occurrence_index``, for the same reason.
    ``maxsize`` is small on purpose. An entry holds a full merged copy of every dictionary in
    the combinator plus its lazy views, tens to hundreds of MB, while a real config has a
    handful of distinct dict tuples at most."""
    sorted_dict_names = tuple(sorted(dict_names_tuple))
    return CombinedOccurrenceIndex(list(sorted_dict_names), normalize_kana, honorific_folding)

def expand_dict_names(dict_str: str) -> List[str]:
    """Resolve the dict spec of an ``occurrences:`` term to a de-duplicated list of
    dictionary names. Accepts a single name (``Foo``), a bracketed combinator
    (``[A,B,C]``), and the ``all`` keyword (expands to every dict in user_files)."""
    if dict_str.startswith('[') and dict_str.endswith(']'):
        raw_names = [d.strip() for d in dict_str[1:-1].split(',')]
    else:
        raw_names = [dict_str]

    dict_names: List[str] = []
    for name in raw_names:
        if name == "all":
            dict_names.extend(get_all_dict_names())
        else:
            dict_names.append(name)

    # Duplicates are removed so this acts as a true combinator (first-seen order preserved).
    # The reserved '_seen' folder is dropped so `occurrences:_seen` and `occurrences:[A,_seen]`
    # can never reach the daily seen dicts. Only `seen:N` may.
    return [name for name in dict.fromkeys(dict_names) if name != SEEN_FOLDER]

def occurrence_counter(
    dict_names: List[str],
    *,
    normalize_kana: bool = False,
    combine_word_forms: bool = False,
    prefix_matching: bool = False,
    suffix_matching: bool = False,
    variant_matching: bool = False,
    stem_matching: bool = False,
    compound_matching: bool = False,
    honorific_folding: bool = False,
    prefolded: bool = False,
):
    """``(expression, reading, card_kanji=None) -> int``, with index resolution and flag
    dispatch hoisted OUT of the per-card loop.

    Semantically identical to ``occurrence_count``, which is a one-shot wrapper around this,
    so the two cannot drift. Only the placement of the work differs. Doing it per card cost
    the dict-name tuple allocation, an ``lru_cache`` probe on a seven-element key, and a
    ten-way keyword binding, together 79% of the warm multi-dict path. Callers evaluating
    many notes against one term should build the counter once and call it per note.

    ``prefolded`` says the caller already kana-folded both strings, making the fold here a
    pure re-allocation. ``card_kanji`` is a precomputed ``_kanji_skeleton``. Both are for
    callers evaluating one note against several predicates, and neither changes the result.

    The returned function carries the index it reads as ``count.index``. Callers memoizing
    counts across reorders use its identity to notice a dictionary update, which replaces the
    index object (see updater.clear_caches)."""
    fold = normalize_kana and not prefolded

    if len(dict_names) == 1:
        index = get_occurrence_index(dict_names[0], normalize_kana, honorific_folding)

        def count(expression: str, reading: str, card_kanji: Optional[str] = None) -> int:
            if fold:
                expression = to_hiragana(expression)
                reading = to_hiragana(reading)
            return index.get_total(
                expression,
                reading,
                combine_word_forms=combine_word_forms,
                prefix_matching=prefix_matching,
                suffix_matching=suffix_matching,
                variant_matching=variant_matching,
                stem_matching=stem_matching,
                compound_matching=compound_matching,
                honorific_folding=honorific_folding,
                card_kanji=card_kanji,
            )

        count.index = index
        return count

    combined = get_combined_occurrence_index(
        tuple(dict_names), normalize_kana, honorific_folding
    )

    def count(expression: str, reading: str, card_kanji: Optional[str] = None) -> int:
        if fold:
            expression = to_hiragana(expression)
            reading = to_hiragana(reading)
        return combined.total(
            expression,
            reading,
            card_kanji,
            combine_word_forms=combine_word_forms,
            prefix_matching=prefix_matching,
            suffix_matching=suffix_matching,
            variant_matching=variant_matching,
            stem_matching=stem_matching,
            compound_matching=compound_matching,
        )

    count.index = combined
    return count


def occurrence_count(
    dict_names: List[str],
    expression: str,
    reading: str,
    *,
    normalize_kana: bool = False,
    combine_word_forms: bool = False,
    prefix_matching: bool = False,
    suffix_matching: bool = False,
    variant_matching: bool = False,
    stem_matching: bool = False,
    compound_matching: bool = False,
    honorific_folding: bool = False,
    prefolded: bool = False,
    card_kanji: Optional[str] = None,
) -> int:
    """Total occurrence count for ``(expression, reading)`` across ``dict_names``, honoring all
    eight lookup flags. Callers must ensure expression and reading are present. A note missing
    either should be treated as a non-match upstream rather than fed a 0 here.

    One-shot convenience wrapper over ``occurrence_counter``. Anything evaluating more than a
    handful of notes should build the counter once instead."""
    return occurrence_counter(
        dict_names,
        normalize_kana=normalize_kana,
        combine_word_forms=combine_word_forms,
        prefix_matching=prefix_matching,
        suffix_matching=suffix_matching,
        variant_matching=variant_matching,
        stem_matching=stem_matching,
        compound_matching=compound_matching,
        honorific_folding=honorific_folding,
        prefolded=prefolded,
    )(expression, reading, card_kanji)
