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

def _is_phrase_entry(expression: str, reading: Optional[str]) -> bool:
    """Structural test for the single-kanji phrase rule: kanji head, whitelisted
    particle second, and a reading to validate against. The tail is OPTIONAL — a bare
    'X<particle>' adverbial (俗に, 特に, 既に) is as much a use of X as 'X<particle><tail>'
    is, and requiring a tail was the only thing keeping those out. Shared with
    seen_manager.build_seen_day so the counting and boolean sides can't drift."""
    return (
        bool(reading)
        and len(expression) >= 2
        and expression[1] in _PHRASE_PARTICLES
        and is_kanji(expression[0])
    )

def _is_suffix_phrase_entry(expression: str, reading: Optional[str]) -> bool:
    """Tail mirror of _is_phrase_entry for the single-kanji *suffix* phrase rule: kanji
    tail, whitelisted particle right before it, non-empty head, and a reading to validate
    against (母の日/ははのひ credits 日/ひ). Shared with seen_manager.build_seen_day so the
    counting and boolean sides can't drift."""
    return (
        bool(reading)
        and len(expression) >= 3
        and expression[-2] in _PHRASE_PARTICLES
        and is_kanji(expression[-1])
    )

def _is_suru_entry(expression: str, reading: Optional[str]) -> bool:
    """Structural test for the single-kanji suru-verb rule: a reading to validate against and
    a written form that is exactly one kanji plus する/じる/ずる (屯する, 愛する, 感じる, 信ずる).

    The single-KANJI head is load-bearing, not decorative. A '㋕'-marked entry is re-keyed
    under its reading by _build_index_from_raw, so expr_to_count can hold 'たむろする'; under
    combine_word_forms, get_total calls prefix_total(reading), and a kana reading is long
    enough to clear _MIN_PREFIX_LENGTH, so that path already credits the entry. Requiring a
    kanji head is exactly what keeps this rule from crediting it a second time.

    Deliberately no tail and no infix: 重んじる is 重 + んじる, not 重 + じる, and never matches.
    Shared with seen_manager.build_seen_day so the counting and boolean sides can't drift."""
    return (
        bool(reading)
        and len(expression) == 3
        and expression.endswith(_SURU_SUFFIXES)
        and is_kanji(expression[0])
    )

def _suru_reading_matches(card_reading: str, entry_reading: str, suffix: str) -> bool:
    """Reading validation for the suru rule: the entry must read as the card's reading plus the
    suffix, so a 屯/たむろ card takes 屯する/たむろする while a 屯/とん card does not (different
    reading, and with it a meaning that no longer tracks the card).

    The one tolerance is the regular sokuon change before する: 察/さつ -> 察する/さっする. That is
    the same reading undergoing a predictable euphony, not a different one, and it carries an
    eighth of the rule (発/接/決/達/脱/失/滅/罰/律/徹/喫/屈/欲 all take it). Gated to する because
    っじる/っずる do not occur, and to readings of 2+ morae so a bare つ cannot degenerate into
    a bare っする.

    Known imprecision, accepted as the price of the recall: a matching reading is not proof of
    a matching sense, so 課/か <- 課する, 辞/じ <- 辞する, 目/もく <- 目する and 上/うわ <- 上ずる
    all get credit. And じる is not always a suffix — 恥じる is 恥/はじ + る, so the natural
    恥/はじ card is missed while a 恥/は card would be credited (same for 閉じる, 混じる, 交じる).

    Shared with seen_manager._suru_present so the counting and boolean sides can't drift."""
    if entry_reading == card_reading + suffix:
        return True
    return (
        suffix == "する"
        and len(card_reading) >= 2
        and card_reading.endswith("つ")
        and entry_reading == card_reading[:-1] + "っする"
    )

def _stem_candidates(expression: str, reading: str) -> List[Tuple[str, str]]:
    """The (expression, reading) pairs a dictionary-form card should also be credited for:
    its 連用形 (戒める -> 戒め, 遊ぶ -> 遊び) and, for い-adjectives, its さ/み/げ nominalizations
    (強い -> 強さ, 痛い -> 痛み, 寂しげ). Forward only — the card is the base form and the entry
    the derived one.

    The conjugation class is unknowable without a dictionary, so a る-final card yields BOTH the
    ichidan (drop る) and godan (る -> り) candidates and lets the index arbitrate: the wrong-class
    form is essentially never a real entry, and the exact (expression, reading) probe in stem_total
    is what proves it. Measured over 13 dictionaries, only 3 of 827 verb gains had both hit.

    Two gates carry the rule, and neither is decorative:

      * ``expression == reading`` is REJECTED. The whole safety argument is that a candidate must
        match on expression AND reading; when they are identical the probe degenerates to a single
        kana lookup that validates nothing. _build_index_from_raw re-keys every '㋕' entry under its
        reading, so the index is full of (kana, kana) pairs for such a probe to hit — measured, this
        gate drops 49% of the rule's raw credit (それる<-それ, ほうる<-ほう, わたす<-わたし) and zero
        legitimate matches. It also makes the rule stable under kana_normalization, which otherwise
        turns every ウ段-final katakana loanword into a verb candidate.
      * ``expression[-1] == reading[-1]`` — the okurigana invariant. The edit is only valid on both
        strings when they end in the same kana, which is precisely when the tail IS okurigana. One
        comparison, and it rejects every kanji-final card (30,510 of 40,449 pairs measured).

    Candidates shorter than _MIN_STEM_LENGTH are dropped: the bare-kanji noun blowups (神る->神,
    太る->太) outweigh the correct single-kanji stems (見る->見, 出る->出) they sit beside. The tail
    mirror of the length gates on prefix_total/_suffix_eligible.

    する is NOT handled — it is irregular, so 勉強する yields 勉強す, never the correct 勉強し (no hits,
    so it costs nothing). じる/ずる verbs DO work (感じる -> 感じ) because they inflect as ichidan.
    Note this differs from _is_suru_entry above, which does special-case all three.

    Shared with seen_manager so the counting and boolean sides can't drift."""
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
        for suffix in _ADJ_NOMINALIZERS:
            out.append((expression[:-1] + suffix, reading[:-1] + suffix))
    return [pair for pair in out if len(pair[0]) >= _MIN_STEM_LENGTH]

def _kanji_skeleton(expression: str) -> str:
    """The deduplicated kanji of a written form, in first-appearance order (煌燦めく -> 煌燦,
    人々 -> 人 since 々 is not a kanji). Empty for kana-only forms, which is exactly what gates
    those out of variant matching. Shared with seen_manager so the counting and boolean sides
    can't drift."""
    out: List[str] = []
    for ch in expression:
        if is_kanji(ch) and ch not in out:
            out.append(ch)
    return "".join(out)

def _variant_kanji_compatible(card_kanji: str, entry_kanji: str) -> bool:
    """The "like enough" test for variant matching: both skeletons non-empty AND one's kanji
    set nested inside the other's. Equal sets are the okurigana case (煌く/煌めく, 落葉/落ち葉,
    子ども/子供 once deduplicated); a strict superset is the added-kanji case (煌めく←煌燦めく).

    Nesting rather than mere intersection is the whole point: same-reading homophone pairs
    usually DO share one kanji but never nest (科学/化学, 保証/保障, 対象/対照, 開放/解放,
    私立/市立), so requiring nesting keeps genuinely different words apart. Shared with
    seen_manager so the counting and boolean sides can't drift."""
    if not card_kanji or not entry_kanji:
        return False
    a, b = set(card_kanji), set(entry_kanji)
    return a <= b or b <= a

def _is_variant_entry(expression: str, reading: Optional[str]) -> bool:
    """Structural test for the variant rule: the entry needs a reading to key on (variants are
    grouped by identical reading) and at least one kanji to share. Shared with
    seen_manager.build_seen_day so the counting and boolean sides can't drift."""
    return bool(reading) and any(is_kanji(ch) for ch in expression)

def _honorific_fold_allowed(stripped: str, vocab) -> bool:
    """Whether an honorific-stripped remainder may be registered as a fold target.
    Allowed when the dict independently recognizes it, OR when it carries a kanji
    (お茶の間→茶の間, お金→金 — near-certainly the same lexeme). Kana-only strips
    stay gated on dict membership: that is where the unrelated-word junk lives
    (おかず→かず, おはよう→はよう). Shared with seen_manager.build_seen_day so the
    counting and boolean sides can't drift."""
    return bool(stripped) and (stripped in vocab or any(is_kanji(ch) for ch in stripped))

def _suffix_eligible(expression: str) -> bool:
    """Whether a card expression may receive *bare* suffix-matching credit. Gate:
    length >= 2 AND contains a kanji. That set is exactly "real words" — 2+ kanji terms
    (学校, 目的) and single-kanji-plus-okurigana words (食べる, 見る, 強い) — which carry
    their reading/meaning across compounds, while excluding bare single kanji (日/手/語,
    whose reading/meaning does not carry) and pure kana (する/こと/しい, katakana loanwords)
    that would match far too broadly. This is the tail mirror of _MIN_PREFIX_LENGTH; single
    kanji return only via the reading-validated tail phrase carve-out
    (single_kanji_suffix_phrase_total; the head-side length gate has two such carve-outs,
    single_kanji_phrase_total and single_kanji_suru_total). Shared with the honorific-fold subsumption in
    get_total and the seen boolean twin so the sides can't drift."""
    return len(expression) >= _MIN_SUFFIX_LENGTH and any(is_kanji(ch) for ch in expression)
_COMBINED_MEMO_CAP = 50_000

# Reserved child folder under user_files holding the daily seen dicts
# (user_files/_seen/<YYYY-MM-DD>/term_meta_bank_*.json). The leading underscore keeps it
# visually distinct and sorted above real dictionaries. It is owned by the `seen:N`
# search term (see seen_manager.py) and must never be treated as a normal occurrence
# dictionary, so it is excluded from dict enumeration / expansion / updating.
SEEN_FOLDER = "_seen"

class OccurrenceIndex:
    def __init__(self) -> None:
        self.expr_to_count: Dict[str, int] = {}
        self.expr_reading_to_count: Dict[Tuple[str, str], int] = {}
        self.honorific_to_count: Dict[str, int] = {}
        # Built lazily on first prefix query (see _ensure_prefix_index).
        self._prefix_exprs: Optional[List[str]] = None
        self._prefix_cumsum: List[int] = []
        # Built lazily on first suffix query (see _ensure_suffix_index): expressions sorted by
        # their reversed form, with a parallel cumsum — the suffix analogue of the prefix index
        # (d ends with e  <=>  reverse(d) starts with reverse(e)).
        self._suffix_revs: Optional[List[str]] = None
        self._suffix_cumsum: List[int] = []
        # Built lazily on first single-kanji phrase query (see _ensure_phrase_index).
        self._phrase_index: Optional[Dict[str, List[Tuple[str, str, int]]]] = None
        # Built lazily on first single-kanji suffix phrase query (see _ensure_suffix_phrase_index).
        self._suffix_phrase_index: Optional[Dict[str, List[Tuple[str, str, int]]]] = None
        # Built lazily on first single-kanji suru query (see _ensure_suru_index).
        self._suru_index: Optional[Dict[str, List[Tuple[str, str, int]]]] = None
        # Built lazily on first variant query (see _ensure_variant_index): reading -> the
        # kanji-bearing forms carrying it, as (expression, count). No skeleton is cached.
        self._variant_index: Optional[Dict[str, List[Tuple[str, int]]]] = None

    def add(self, expression: str, reading: Optional[str], count: int) -> None:
        if reading:
            key = (expression, reading)
            self.expr_reading_to_count[key] = self.expr_reading_to_count.get(key, 0) + count

        # Always fallback to expression alone to account for reading mismatches
        # Accumulate counts for the same expression
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
        """Sum the counts of all terms that have ``expression`` as a *strict*
        prefix (longer terms only — the exact match is credited by ``get``).

        Computed via binary search over a lazily-built sorted index, so there is
        no per-term prefix explosion at build time."""
        if len(expression) < _MIN_PREFIX_LENGTH:
            return 0
        self._ensure_prefix_index()
        exprs = self._prefix_exprs
        # U+10FFFF is the max code point, so every term starting with `expression`
        # sorts before the sentinel — including terms whose next char is a
        # supplementary-plane kanji like 𠮟 (U+FFFF would sort before those).
        lo = bisect.bisect_left(exprs, expression)
        hi = bisect.bisect_left(exprs, expression + chr(0x10FFFF))
        if lo < len(exprs) and exprs[lo] == expression:
            lo += 1  # exclude the exact match (counted separately by get)
        return self._prefix_cumsum[hi] - self._prefix_cumsum[lo]

    def _ensure_suffix_index(self) -> None:
        if self._suffix_revs is not None:
            return
        # Reverse each expression ONCE into a keyed map, then sort the reversed strings
        # directly. Sorting the forward keys under a `key=lambda kv: kv[0][::-1]` reversed
        # every expression a second time when materializing the list below; sorting
        # (reversed, count) tuples instead trades string comparison for tuple comparison
        # and is slower still. The reversal is injective, so no two entries collide.
        rev_to_count = {expr[::-1]: count for expr, count in self.expr_to_count.items()}
        revs = sorted(rev_to_count)
        cumsum = [0]
        for rev in revs:
            cumsum.append(cumsum[-1] + rev_to_count[rev])
        self._suffix_revs = revs
        self._suffix_cumsum = cumsum

    def suffix_total(self, expression: str) -> int:
        """Sum the counts of all terms that have ``expression`` as a *strict* written
        suffix (longer terms only — the exact match is credited by ``get``), gated to
        kanji-bearing card expressions (see ``_suffix_eligible``).

        The suffix mirror of ``prefix_total``: a binary search over a lazily-built index of
        reversed expressions, since ``d`` ends with ``e`` iff ``reverse(d)`` starts with
        ``reverse(e)``. Same O(log n) cost, no per-term suffix explosion at build time."""
        if not _suffix_eligible(expression):
            return 0
        self._ensure_suffix_index()
        revs = self._suffix_revs
        rev = expression[::-1]
        # Same U+10FFFF sentinel trick as prefix_total, applied over the reversed strings, so
        # every term ending with `expression` sorts before the sentinel.
        lo = bisect.bisect_left(revs, rev)
        hi = bisect.bisect_left(revs, rev + chr(0x10FFFF))
        if lo < len(revs) and revs[lo] == rev:
            lo += 1  # exclude the exact match (counted separately by get)
        return self._suffix_cumsum[hi] - self._suffix_cumsum[lo]

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
        """Phrase credit for a single-kanji card: sums entries 'X<particle>' with an
        optional tail, whose reading starts with the card's reading + the particle,
        validating that X is read in-context as the card reads it (手を貸す/てをかす and
        俗に/ぞくに credit 手/て and 俗/ぞく, but 手/しゅ gets nothing). One of the two
        carve-outs complementing prefix_total, which gates out single-character expressions
        entirely; the other is single_kanji_suru_total."""
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
        """Tail mirror of single_kanji_phrase_total: phrase credit for a single-kanji card
        from entries '<head><particle>X' whose reading ends with the particle + the card's
        reading, validating that X is read in-context as the card reads it (母の日/ははのひ
        credits 日/ひ but not 日/にち). An EXACT match — particles sit on a word boundary, so
        the tail kanji never rendaku's. Complements suffix_total, which gates out
        single-character expressions entirely."""
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
        感じる/かんじる, but 屯/とん takes nothing). The verb's meaning tracks the bare form's
        closely enough that a card for X is worth prioritizing off the verb's occurrences.

        The third complement to prefix_total, which gates out single-character expressions
        entirely — す/じ/ず are not phrase particles, so single_kanji_phrase_total could never
        reach these. Nothing else double-counts them either: the entry ends in る (not in X) so
        the suffix rules miss it, and its reading is strictly longer than the card's, so
        variant_total — which buckets on an IDENTICAL reading — cannot see it."""
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

        Deliberately stores NO kanji skeleton: a bucket averages under two forms, so recomputing
        the handful that a query actually touches beats skeletonizing every entry up front —
        measured 28% off the build and 35% off the retained memory across 10 dictionaries.

        ``_is_variant_entry`` is also what keeps kana-only entries out of the index entirely, which
        is what makes a kana dict entry unable to credit a kanji card (that bridge belongs to
        combine_word_forms). Do not relax the gate."""
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
        reading whose kanji nest with the card's (see _variant_kanji_compatible). This is the one
        rule that bridges okurigana and alternate-spelling differences, which the written
        prefix/suffix indexes structurally cannot see — 煌く is neither a prefix nor a suffix of
        煌めく. Kana-only forms have an empty skeleton and so never participate on either side.

        Grouping by reading keeps the candidate list per query tiny (the handful of forms sharing
        one exact reading), so this is a dict lookup plus a few short-string comparisons.

        ``card_kanji`` is an optional precomputed ``_kanji_skeleton(expression)`` — purely an
        optimization for the multi-dict path, where CombinedOccurrenceIndex would otherwise
        recompute the same skeleton once per dictionary. It must always equal what this function
        would derive itself; it can never change the result. Left None, it is derived here.

        ``prefix_matching``/``suffix_matching`` are DEDUP GUARDS, not widening knobs: when those
        flags are on, a variant that is also a strict written prefix/suffix of the card expression
        was already summed by prefix_total/suffix_total (card 気持/きもち + entry 気持ち/きもち),
        so it is skipped here instead of counted twice — the same subsumption reasoning as the
        honorific/suffix guard in get_total."""
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
                continue  # the card's own form — credited by get()
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
        """Sum the counts of the card's 連用形 / さ・み・げ forms — the one rule that bridges a
        dictionary-form card to the derived noun an occurrence dict lists separately (戒める takes
        戒め, 遊ぶ takes 遊び, 強い takes 強さ). See _stem_candidates for how the candidates are
        built and why its two gates are load-bearing.

        Forward only, deliberately. The reverse (a 連用形 card taking its dictionary form) was
        measured and rejected: a rare derived form would inherit the count of a far commoner base
        (無げ, base 1, would take 無い's 8484), inverting the priority ordering this addon exists to
        produce. Forward has the opposite property — a large transfer only happens when the derived
        form genuinely IS the commoner form of the same word (匂う <- 匂い), which is exactly when
        the credit is deserved. prefix_matching still covers the ichidan half of the reverse.

        Probes ``expr_reading_to_count`` DIRECTLY and must never route through ``self.get`` — get
        carries a per-dict reading-mismatch fallback that is the one non-linear term in the index.
        Using it here would still pass every single-dict test while silently breaking the
        merged-vs-per-dict drift guard for multi-dict combinators only. Both maps this reads are
        plain per-dict sums, which is what keeps sum_d stem_total_d == stem_total_merged.

        Needs no lazy view of its own — both maps are eager — so unlike every other rule here this
        is a few dict lookups behind two character tests, with no build cost and no retained memory.

        No dedup guard, and it is the only rule in this file that needs none. Every candidate either
        shortens the card (ichidan) or replaces its final character (godan, adjective), so:
        prefix_total needs strictly LONGER terms; suffix_total needs terms ending with the whole
        expression; variant_total buckets on the card's EXACT reading and every candidate reading
        differs; honorific_to_count is keyed on entries a character longer; get is keyed on the card
        itself. The single-kanji carve-outs are unreachable too — a 1-character card either fails the
        okurigana invariant or yields a candidate below _MIN_STEM_LENGTH. Two near-misses worth
        naming: cand_expr == cand_reading iff expression == reading (gated in _stem_candidates), and
        cand_expr == reading is impossible because the replacement character differs from
        expression[-1] == reading[-1] by construction."""
        candidates = _stem_candidates(expression, reading)
        if not candidates:
            return 0
        pair_counts = self.expr_reading_to_count
        total = 0
        for cand_expr, cand_reading in candidates:
            total += pair_counts.get((cand_expr, cand_reading), 0)
        if combine_word_forms:
            # Mirror of the combine_word_forms term in get_total: a kana-only entry is keyed under
            # its reading, so the stem's kana form is credited from expr_to_count. reading_is_distinct
            # is guaranteed here — _stem_candidates rejects expression == reading — so the two terms
            # can never be the same entry counted twice.
            for _cand_expr, cand_reading in candidates:
                total += self.expr_to_count.get(cand_reading, 0)
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
        honorific_folding: bool = False,
        card_kanji: Optional[str] = None,
    ) -> int:
        """``card_kanji`` is forwarded to variant_total as a precomputed skeleton; see there. It is
        an optimization hint only and must match what variant_total would derive from
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
            # No reading-side term (unlike prefix): a kana reading is never suffix-eligible
            # (contains no kanji), so suffix_total(reading) is a definitional no-op.
            total += self.suffix_total(expression)
            # Single-kanji cards (gated out of the bare path) return only via the tail phrase
            # carve-out — the mirror of single_kanji_phrase_total.
            total += self.single_kanji_suffix_phrase_total(expression, reading)
        if variant_matching:
            # No reading-side term (unlike combine_word_forms x prefix_matching above): a kana
            # reading has an empty kanji skeleton, so variant_total(reading) is a definitional
            # no-op. The prefix/suffix flags are passed only so overlapping credit already taken
            # by prefix_total/suffix_total is skipped — see variant_total.
            total += self.variant_total(
                expression,
                reading,
                card_kanji=card_kanji,
                prefix_matching=prefix_matching,
                suffix_matching=suffix_matching,
            )
        if stem_matching:
            # No reading-side term of its own beyond the one inside stem_total: a kana reading is
            # identical to its own stem candidates' readings, and _stem_candidates rejects the
            # expression == reading case outright, so stem_total(reading, reading) is a no-op.
            total += self.stem_total(
                expression, reading, combine_word_forms=combine_word_forms
            )
        if honorific_folding:
            # honorific_to_count credits the bare form from an 'お/ご/御 + form' entry, which is
            # a strict written suffix of that entry — so when the expression is suffix-eligible,
            # suffix_total(expression) already counted it. Skip the expression-side term then to
            # avoid double-counting. Kana-only folds (かず←おかず) are not suffix-eligible, so they
            # are never subsumed and keep their credit; the reading side is kana-tailed likewise.
            if not (suffix_matching and _suffix_eligible(expression)):
                total += self.honorific_to_count.get(expression, 0)
            if combine_word_forms and reading_is_distinct:
                total += self.honorific_to_count.get(reading, 0)
        return total

class CombinedOccurrenceIndex(OccurrenceIndex):
    """One MERGED index over several dictionaries, not a loop over them.

    Summing ``get_total`` across N per-dict indexes costs N times as much per card and
    materializes N sets of lazy prefix/suffix/variant views. Folding the dictionaries into a
    single index up front makes every lookup O(1) in the dictionary count (measured 33.5 ->
    9.6 us/card at 4 dicts, and flat as dicts are added) and builds one set of views.

    The fold is EXACTLY equivalent to the old per-dict sum, not an approximation:

      * ``expr_to_count`` / ``expr_reading_to_count`` are plain sums, and every rule that
        reads them (combine_word_forms, prefix/suffix totals, the phrase rules, variant
        matching) sums linearly over entries whose gates are structural tests on the query
        or on one entry's own strings — never on a dictionary's contents. So
        sum_d sum_entries == sum over the merged entries.
      * ``honorific_to_count`` is NOT rebuilt from the merged vocabulary: its
        ``_honorific_fold_allowed`` gate consults the dictionary's own expr_to_count, and the
        merged vocabulary is a superset that would admit folds no single dict allowed. Each
        dict's map is built under its own gate (by _build_index_from_raw) and only then summed.
      * ``get`` is the one non-linear term, because of its per-dict reading-mismatch fallback
        (pair count if the dict has the pair, else the dict's expression total). That can't
        collapse into either merged map, so ``_base_total`` precomputes it per pair; see
        ``_fold``.

    A drift-guard test pins merged totals against the per-dict sum across every flag
    combination.

    Only the two BUILD-time flags are constructor state, for the same reason
    ``get_occurrence_index`` keys on only those: combine/prefix/suffix/variant/stem are
    query-time flags that neither the fold nor any lazy view reads, so an index built
    under one combination is byte-identical to one built under another. They are passed
    to ``total`` instead — keeping them in the cache key left up to 32 identical merged
    copies of every dictionary resident after a few config flips, and a merged copy is an
    order of magnitude larger than a single-dict index."""

    def __init__(self, dict_names: List[str], normalize_kana: bool = False, honorific_folding: bool = False) -> None:
        super().__init__()
        self.dict_names = sorted(dict_names)
        self.normalize_kana = normalize_kana
        self.honorific_folding = honorific_folding
        # (expression, reading) -> what the per-dict `get` fallback would have summed to.
        # Filled by _fold; a pair absent from every dict falls back to expr_to_count.
        self._base_total: Dict[Tuple[str, str], int] = {}
        self._folded = False
        # Per-card memo of finished totals. MUST stay separate from expr_reading_to_count:
        # that now holds real dictionary data, and the FIFO eviction below would silently
        # delete entries the fold can never recompute. Totals depend on the query-time
        # flags, which are fixed for a run but not for the object's lifetime, so the memo
        # is dropped whenever they change (see total) rather than widening every key.
        self._memo: Dict[Tuple[str, str], int] = {}
        self._memo_flags: Optional[Tuple[bool, bool, bool, bool, bool]] = None

    def _fold(self) -> None:
        """Merge every dictionary into this index. Lazy: a combined index that is never
        queried (a search whose standard part matched nothing) never pays for it."""
        if self._folded:
            return
        self._folded = True

        expr_to_count = self.expr_to_count
        expr_reading_to_count = self.expr_reading_to_count
        honorific_to_count = self.honorific_to_count
        base_total = self._base_total

        # Folded from the SHARED cache rather than a private parse: dictionaries recur
        # across combinators (`occurrences:[A,B]` and `occurrences:[A,C]`), and reparsing
        # A for each would cost more than the fold saves.
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
        """The merged BASE count — the per-dict `get` fallback, summed. Overrides
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
    ) -> int:
        """Flag-inclusive total across every dict for one card — the entry point
        ``occurrence_count``/``occurrence_counter`` use, memoized per card.

        PRECONDITION: ``expression``/``reading`` must already be kana-folded when
        ``normalize_kana`` is set — the callers do that unconditionally before reaching
        either the single-dict or the combined path, and the indexes are keyed on folded
        strings.

        The five query-time flags are arguments rather than constructor state so the merged
        index can be shared across flag combinations (see the class docstring);
        ``honorific_folding`` stays on the instance because it is a build flag —
        ``honorific_to_count`` is only populated when the per-dict indexes were built with it.

        ``card_kanji`` is an optional precomputed skeleton; see ``variant_total``."""
        flags = (combine_word_forms, prefix_matching, suffix_matching, variant_matching,
                 stem_matching)
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

        # to_hiragana leaves CJK ideographs untouched, so this is identical whether or not
        # the caller folded — see the precondition above. Derived after the memo check so
        # repeat lookups don't pay for it, and skipped entirely when the caller already has it.
        if card_kanji is None and variant_matching:
            card_kanji = _kanji_skeleton(expression)

        total_count = self.get_total(
            expression,
            reading,
            combine_word_forms=combine_word_forms,
            prefix_matching=prefix_matching,
            suffix_matching=suffix_matching,
            variant_matching=variant_matching,
            stem_matching=stem_matching,
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
    """Parse the dictionary's term meta bank from disk. Deliberately NOT cached:
    the raw list is huge (every entry of a 100k+ term bank) and only needed while
    building an OccurrenceIndex — get_occurrence_index memoizes the compact result,
    so in the steady state each dict is parsed once per session and the raw list
    is garbage-collected right after the build."""
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

            # Check for '㋕' (kana-only indicator) in display values
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
            # If specifically marked as kana occurrences, attribute to the reading
            # (requires combine_word_forms at lookup time to credit kanji-bearing cards)
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
            # strip one-character honorific prefix (all entries in the tuple are single chars)
            stripped = expr[1:]
            if not _honorific_fold_allowed(stripped, index.expr_to_count):
                continue
            index.honorific_to_count[stripped] = index.honorific_to_count.get(stripped, 0) + count

    return index

@lru_cache(maxsize=64)
def get_occurrence_index(dict_name: str, normalize_kana: bool = False, honorific_folding: bool = False) -> OccurrenceIndex:
    """Parsed index for one dictionary, memoized for the session.

    Only the two BUILD-time flags key this cache. prefix/suffix/variant/stem matching are
    query-time flags: their indexes are lazy views derived from expr_to_count /
    expr_reading_to_count (stem matching has no view at all), so an index built with them off is identical to one built with
    them on, and the view is materialized on first use either way. Keying on them used to
    leave up to 16 byte-identical copies of the same dictionary resident after a few flag
    flips (measured 8.1 MB per dict with every view built)."""
    data = _load_term_meta_raw(dict_name)
    if data is None:
        return OccurrenceIndex()
    return _build_index_from_raw(data, normalize_kana, honorific_folding)

@lru_cache(maxsize=4)
def get_combined_occurrence_index(dict_names_tuple: Tuple[str, ...], normalize_kana: bool = False, honorific_folding: bool = False) -> CombinedOccurrenceIndex:
    """Merged index for one combinator, memoized for the session.

    Keyed on the same two BUILD-time flags as ``get_occurrence_index``, for the same reason
    (see ``CombinedOccurrenceIndex``). ``maxsize`` is small on purpose: an entry holds a full
    merged copy of every dictionary in the combinator plus its lazy views — tens to hundreds
    of MB — while a real config has a handful of distinct dict tuples at most."""
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

    # Remove duplicates to act as a true combinator (preserves first-seen order), and
    # drop the reserved '_seen' folder so `occurrences:_seen` / `occurrences:[A,_seen]`
    # can never reach the daily seen dicts — only `seen:N` may.
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
    honorific_folding: bool = False,
    prefolded: bool = False,
):
    """``(expression, reading, card_kanji=None) -> int``, with index resolution and flag
    dispatch hoisted OUT of the per-card loop.

    Semantically identical to ``occurrence_count`` — that function is now a one-shot wrapper
    around this one, so the two cannot drift. The difference is purely where the work
    happens: ``occurrence_count`` re-did the dict-name tuple allocation, the ``lru_cache``
    probe on a seven-element key, and the nine-way keyword binding for *every card*, which
    measured as 79% of the warm multi-dict path. Callers evaluating many notes against one
    term (``DataManager._term_predicate``, ``search.resolve_occurrences``) should build the
    counter once and call it per note.

    ``prefolded`` says the caller already kana-folded both strings, so the fold here would be
    a pure re-allocation; ``card_kanji`` is a precomputed ``_kanji_skeleton``. Both are for
    callers that evaluate one note against several predicates (see
    ``DataManager._note_derived``) and neither can change the result."""
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
                honorific_folding=honorific_folding,
                card_kanji=card_kanji,
            )

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
        )

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
    honorific_folding: bool = False,
    prefolded: bool = False,
    card_kanji: Optional[str] = None,
) -> int:
    """Total occurrence count for ``(expression, reading)`` across ``dict_names``,
    honoring all seven lookup flags. Mirrors the body of the former
    ``OccurrenceRule.matches`` so both the reorder path and the browser/API search
    term resolve identically. Callers must ensure expression/reading are present;
    a note missing either should be treated as a non-match upstream rather than
    fed a 0 here.

    One-shot convenience wrapper over ``occurrence_counter``; anything evaluating more than
    a handful of notes should build the counter once instead."""
    return occurrence_counter(
        dict_names,
        normalize_kana=normalize_kana,
        combine_word_forms=combine_word_forms,
        prefix_matching=prefix_matching,
        suffix_matching=suffix_matching,
        variant_matching=variant_matching,
        stem_matching=stem_matching,
        honorific_folding=honorific_folding,
        prefolded=prefolded,
    )(expression, reading, card_kanji)
