import itertools

import pytest

import dictionary_manager as dm
from dictionary_manager import (
    CombinedOccurrenceIndex,
    OccurrenceIndex,
    _build_index_from_raw,
    _stem_candidates,
    expand_dict_names,
    occurrence_count,
)


# expand_dict_names

def test_expand_single_name():
    assert expand_dict_names("Foo") == ["Foo"]


def test_expand_bracketed_list_strips_and_dedups():
    assert expand_dict_names("[A, B , A]") == ["A", "B"]


def test_expand_all_keyword(monkeypatch):
    monkeypatch.setattr(dm, "get_all_dict_names", lambda: ["D1", "D2"])
    assert expand_dict_names("all") == ["D1", "D2"]


def test_expand_all_inside_list_merges_and_dedups(monkeypatch):
    monkeypatch.setattr(dm, "get_all_dict_names", lambda: ["D1", "D2"])
    assert expand_dict_names("[D1,all]") == ["D1", "D2"]


# OccurrenceIndex.get_total flags

def _index():
    idx = OccurrenceIndex()
    idx.add("彫刻", "ちょうこく", 5)
    idx.add("彫刻家", "ちょうこくか", 100)
    idx.add("彫刻品", "ちょうこくひん", 30)
    return idx


def test_get_total_exact():
    assert _index().get_total("彫刻", "ちょうこく") == 5


def test_get_total_prefix_matching():
    # exact 5 + 100 + 30 from the longer prefixed terms
    assert _index().get_total("彫刻", "ちょうこく", prefix_matching=True) == 135


def test_get_total_combine_word_forms():
    idx = OccurrenceIndex()
    idx.add("南京", "なんきん", 7)
    idx.add("なんきん", None, 3)  # kana-only entry keyed under the reading
    # exact (expr,reading) pair only -> 7; combine credits the kana-only reading entry -> 10
    assert idx.get_total("南京", "なんきん") == 7
    assert idx.get_total("南京", "なんきん", combine_word_forms=True) == 10


# occurrence_count routing

def test_occurrence_count_single_dict(monkeypatch):
    captured = {}

    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        captured["name"] = name
        idx = OccurrenceIndex()
        idx.add("茶", "ちゃ", 42)
        return idx

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    count = occurrence_count(["MyDict"], "茶", "ちゃ")
    assert count == 42
    assert captured["name"] == "MyDict"


def test_occurrence_count_multi_dict_uses_combined(monkeypatch):
    class FakeCombined:
        def __init__(self):
            self.calls = []

        def total(self, expression, reading, card_kanji=None, **flags):
            self.calls.append((expression, reading))
            return 99

    fake = FakeCombined()
    monkeypatch.setattr(dm, "get_combined_occurrence_index", lambda *a, **k: fake)
    count = occurrence_count(["A", "B"], "茶", "ちゃ")
    assert count == 99
    assert fake.calls == [("茶", "ちゃ")]


def test_occurrence_count_normalize_kana(monkeypatch):
    seen = {}

    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        idx = OccurrenceIndex()
        idx.add("ぎりぎり", "ぎりぎり", 8)  # hiragana key
        return idx

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    # katakana input folds to hiragana before lookup
    count = occurrence_count(["D"], "ギリギリ", "ギリギリ", normalize_kana=True)
    assert count == 8


# _build_index_from_raw: meta shapes & filtering

def test_build_meta_as_int():
    idx = _build_index_from_raw([["猫", "freq", 7]])
    assert idx.get("猫", "x") == 7


def test_build_meta_as_numeric_string():
    idx = _build_index_from_raw([["犬", "freq", "12"]])
    assert idx.get("犬", "x") == 12


def test_build_meta_as_non_numeric_string_is_skipped():
    idx = _build_index_from_raw([["犬", "freq", "NaN"]])
    assert idx.get("犬", "x") == 0  # count stayed 0 -> never added


def test_build_skips_short_and_nonstring_and_zero_entries():
    idx = _build_index_from_raw([
        ["only", "two"],   # fewer than 3 items
        [123, "freq", 5],  # non-string expression
        ["zero", "freq", 0],  # count <= 0
        ["ok", "freq", 5],
    ])
    assert idx.expr_to_count == {"ok": 5}


def test_build_meta_dict_with_frequency_value_and_reading():
    idx = _build_index_from_raw([
        ["彫刻", "freq", {"reading": "ちょうこく", "frequency": {"value": 50}}],
    ])
    assert idx.get("彫刻", "ちょうこく") == 50
    assert idx.expr_reading_to_count[("彫刻", "ちょうこく")] == 50


def test_build_accumulates_counts_for_same_expression():
    # Regression (b9d6b7c): repeated entries for one expression must sum, not overwrite.
    idx = _build_index_from_raw([["茶", "freq", 5], ["茶", "freq", 3]])
    assert idx.get("茶", "x") == 8


# kana-only (㋕) attribution

def test_build_kana_only_indicator_attributes_count_to_reading():
    # Regression (b9d6b7c / 23d3b9b): a ㋕-flagged entry is keyed under the reading,
    # and a kanji-bearing card is only credited via combine_word_forms.
    idx = _build_index_from_raw([
        ["南京", "freq", {"reading": "なんきん",
                          "frequency": {"value": 10, "displayValue": "㋕10"}}],
    ])
    assert idx.expr_to_count.get("なんきん") == 10
    assert idx.expr_to_count.get("南京") is None
    assert idx.get_total("南京", "なんきん") == 0
    assert idx.get_total("南京", "なんきん", combine_word_forms=True) == 10


def test_build_kana_indicator_in_top_level_display_value():
    idx = _build_index_from_raw([
        ["ぶどう", "freq", {"reading": "ぶどう", "displayValue": "㋕", "value": 5}],
    ])
    assert idx.expr_to_count.get("ぶどう") == 5


def test_build_normalize_kana_folds_katakana_keys():
    idx = _build_index_from_raw(
        [["ギリギリ", "freq", {"reading": "ギリギリ", "value": 8}]],
        normalize_kana=True,
    )
    assert idx.get("ぎりぎり", "ぎりぎり") == 8


# honorific folding

def test_build_honorific_folding_credits_stripped_base():
    # Regression (5ae53de): お/ご/御-prefixed terms credit their stripped base.
    idx = _build_index_from_raw(
        [["茶", "freq", 5], ["お茶", "freq", 30]],
        honorific_folding=True,
    )
    assert idx.honorific_to_count.get("茶") == 30
    assert idx.get_total("茶", "ちゃ") == 5
    assert idx.get_total("茶", "ちゃ", honorific_folding=True) == 35


def test_build_honorific_folding_credits_kanji_base_absent_from_dict():
    # A kanji-bearing stripped form folds even when the dict never contains the
    # bare form itself. お茶の間-only media must still credit a 茶の間 card.
    idx = _build_index_from_raw([["お茶の間", "freq", 9]], honorific_folding=True)
    assert idx.honorific_to_count.get("茶の間") == 9
    assert idx.get_total("茶の間", "ちゃのま", honorific_folding=True) == 9


def test_build_honorific_folding_credits_single_kanji_base():
    # The documented お金→金 case: single-kanji remainders are wanted folds too.
    idx = _build_index_from_raw([["お金", "freq", 40]], honorific_folding=True)
    assert idx.honorific_to_count.get("金") == 40


def test_build_honorific_folding_skips_kana_base_absent_from_dict():
    # Kana-only strips stay gated on dict membership. おかず is not お+かず, and
    # blind stripping would hand かず (a plausible real card) a phantom count.
    idx = _build_index_from_raw(
        [["おかず", "freq", 12], ["おはよう", "freq", 50]],
        honorific_folding=True,
    )
    assert idx.honorific_to_count == {}


def test_build_honorific_folding_kana_base_present_in_dict_still_folds():
    # The original dict-membership path is untouched for kana strips.
    idx = _build_index_from_raw(
        [["しゃれ", "freq", 3], ["おしゃれ", "freq", 20]],
        honorific_folding=True,
    )
    assert idx.honorific_to_count.get("しゃれ") == 20


# prefix_total edges

def test_prefix_total_below_min_length_is_zero():
    idx = OccurrenceIndex()
    idx.add("猫", None, 5)
    idx.add("猫又", None, 7)
    assert idx.prefix_total("猫") == 0  # single char < _MIN_PREFIX_LENGTH


def test_prefix_total_excludes_exact_match():
    idx = OccurrenceIndex()
    idx.add("彫刻", None, 5)
    idx.add("彫刻家", None, 100)
    assert idx.prefix_total("彫刻") == 100  # only the longer term, exact handled by get()


def test_prefix_total_includes_supplementary_plane_successors():
    # Regression: with a U+FFFF sentinel, a term whose char right after the prefix
    # is a supplementary-plane kanji (𠮟, U+20B9F) sorted past the range end and
    # was silently missed.
    idx = OccurrenceIndex()
    idx.add("漢字", None, 5)
    idx.add("漢字𠮟", None, 70)
    assert idx.prefix_total("漢字") == 70


# single-kanji phrase matching

def test_phrase_total_credits_particle_phrase():
    idx = OccurrenceIndex()
    idx.add("手", "て", 5)
    idx.add("手を貸す", "てをかす", 10)
    idx.add("手が出る", "てがでる", 3)
    idx.add("手紙", "てがみ", 50)  # no particle -> morpheme compound, excluded
    assert idx.get_total("手", "て") == 5
    # exact 5 + phrase 10 + 3; 手紙 excluded (and prefix_total's single-char gate
    # keeps it out of the bare prefix path -> no double counting)
    assert idx.get_total("手", "て", prefix_matching=True) == 18
    assert idx.prefix_total("手") == 0


def test_phrase_total_reading_validation_excludes_mismatches():
    idx = OccurrenceIndex()
    idx.add("思はず", "おもわず", 9)   # old orthography: は read わ -> reading gate fails
    idx.add("積もる", "つもる", 6)     # okurigana verb
    assert idx.get_total("思", "おも", prefix_matching=True) == 0
    assert idx.get_total("積", "せき", prefix_matching=True) == 0
    # known edge: the implausible truncated reading つ WOULD validate 積もる
    assert idx.get_total("積", "つ", prefix_matching=True) == 6


def test_phrase_total_credits_bare_particle_form():
    idx = OccurrenceIndex()
    idx.add("俗", "ぞく", 3)
    idx.add("俗に", "ぞくに", 120)        # bare 'X<particle>', tail optional
    idx.add("俗に言う", "ぞくにいう", 40)  # ...and a tailed phrase still counts alongside it
    idx.add("特に", "とくに", 100)
    assert idx.get_total("俗", "ぞく") == 3
    assert idx.get_total("俗", "ぞく", prefix_matching=True) == 163
    assert idx.get_total("特", "とく", prefix_matching=True) == 100
    # the reading gate is still what decides which head a bare form credits
    assert idx.get_total("特", "しょく", prefix_matching=True) == 0


# single-kanji suru verbs

def test_suru_total_credits_single_kanji_verb():
    idx = OccurrenceIndex()
    idx.add("屯", "たむろ", 5)
    idx.add("屯する", "たむろする", 50)
    idx.add("感じる", "かんじる", 20)
    idx.add("信ずる", "しんずる", 10)
    assert idx.get_total("屯", "たむろ") == 5
    assert idx.get_total("屯", "たむろ", prefix_matching=True) == 55
    assert idx.get_total("感", "かん", prefix_matching=True) == 20
    assert idx.get_total("信", "しん", prefix_matching=True) == 10
    # the single-char gate keeps the bare prefix path out of it -> no double counting
    assert idx.prefix_total("屯") == 0


def test_suru_total_reading_validation_excludes_mismatches():
    idx = OccurrenceIndex()
    idx.add("屯する", "たむろする", 50)   # the noun 屯 also reads とん; that card is a different word
    idx.add("訳する", "やくする", 7)      # vs a 訳/わけ card ("reason"), a different word
    idx.add("課する", "かする", 4)
    assert idx.get_total("屯", "とん", prefix_matching=True) == 0
    assert idx.get_total("訳", "わけ", prefix_matching=True) == 0
    assert idx.get_total("屯", "たむろ", prefix_matching=True) == 50
    # known imprecision: a matching reading is not proof of a matching sense
    assert idx.get_total("課", "か", prefix_matching=True) == 4


def test_suru_total_sokuon_allowance():
    idx = OccurrenceIndex()
    idx.add("察する", "さっする", 30)
    idx.add("達する", "たっする", 12)
    idx.add("津する", "っする", 3)        # degenerate: a bare つ card must not reach it
    assert idx.get_total("察", "さつ", prefix_matching=True) == 30
    assert idx.get_total("達", "たつ", prefix_matching=True) == 12
    # the allowance is つ -> っ only; an unrelated reading still gets nothing
    assert idx.get_total("察", "さち", prefix_matching=True) == 0
    # ...and a bare つ reading must not degenerate into a bare っする
    assert idx.get_total("津", "つ", prefix_matching=True) == 0


def test_suru_total_requires_exact_single_kanji_stem():
    idx = OccurrenceIndex()
    idx.add("重んじる", "おもんじる", 5)   # 重 + んじる, not 重 + じる
    idx.add("勉強する", "べんきょうする", 100)
    idx.add("恥じる", "はじる", 8)         # 恥/はじ + る, so じる is not a suffix here
    idx.add("屯する", None, 9)             # no reading -> nothing to validate against
    assert idx.get_total("重", "おも", prefix_matching=True) == 0
    assert idx.get_total("恥", "はじ", prefix_matching=True) == 0
    assert idx.get_total("屯", "たむろ", prefix_matching=True) == 0
    assert idx.get_total("勉", "べん", prefix_matching=True) == 0
    # a multi-char card still arrives via prefix_total, exactly once
    assert idx.get_total("勉強", "べんきょう", prefix_matching=True) == 100
    assert idx.get_total("勉強", "べんきょう") == 0


def test_suru_total_off_without_prefix_matching():
    idx = OccurrenceIndex()
    idx.add("屯する", "たむろする", 50)
    assert idx.get_total("屯", "たむろ") == 0
    assert idx.get_total("屯", "たむろ", suffix_matching=True, variant_matching=True,
                         honorific_folding=True, combine_word_forms=True) == 0


def test_suru_total_no_double_count_with_kana_rekeyed_entry():
    """A ㋕-marked 屯する is re-keyed under たむろする, where prefix_total(reading) already
    credits it under combine_word_forms. The rule's kanji-head requirement is what keeps it
    from being counted a second time."""
    idx = OccurrenceIndex()
    idx.add("たむろする", "たむろする", 50)   # what _build_index_from_raw stores for a ㋕ entry
    assert idx.get_total("屯", "たむろ", prefix_matching=True) == 0
    assert idx.get_total("屯", "たむろ", prefix_matching=True, combine_word_forms=True) == 50


def test_phrase_total_requires_kanji_head_and_reading():
    idx = OccurrenceIndex()
    idx.add("手を貸す", "てをかす", 10)
    idx.add("とはいえ", "とはいえ", 5)   # kana head never enters the phrase bucket
    idx.add("手を出す", None, 9)         # no reading -> nothing to validate against
    assert idx.get_total("と", "と", prefix_matching=True) == 0
    assert idx.get_total("手", "", prefix_matching=True) == 0    # empty card reading
    assert idx.get_total("手", "しゅ", prefix_matching=True) == 0
    assert idx.get_total("手", "て", prefix_matching=True) == 10  # 手を出す contributes nothing


def test_phrase_total_homograph_readings():
    idx = OccurrenceIndex()
    idx.add("角を曲がる", "かどをまがる", 7)
    assert idx.get_total("角", "かど", prefix_matching=True) == 7
    assert idx.get_total("角", "つの", prefix_matching=True) == 0


def test_phrase_kana_only_entry_never_lands_in_kanji_bucket():
    # A ㋕-flagged phrase is keyed under its kana reading, so its head is kana
    # and it can never credit a kanji card via the phrase path.
    idx = _build_index_from_raw([
        ["手を貸す", "freq", {"reading": "てをかす",
                              "frequency": {"value": 10, "displayValue": "㋕10"}}],
    ])
    assert idx.get_total("手", "て", prefix_matching=True) == 0


def test_phrase_total_normalize_kana_end_to_end(monkeypatch):
    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        return _build_index_from_raw(
            [["手を貸す", "freq", {"reading": "テヲカス", "value": 10}]],
            normalize_kana=normalize_kana,
        )

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    # katakana card reading folds to hiragana before the phrase-reading comparison
    count = occurrence_count(["D"], "手", "テ", normalize_kana=True, prefix_matching=True)
    assert count == 10


def test_phrase_total_stacks_with_honorific_folding():
    idx = _build_index_from_raw(
        [
            ["手", "freq", {"reading": "て", "value": 5}],
            ["手を貸す", "freq", {"reading": "てをかす", "value": 10}],
            ["お手", "freq", {"reading": "おて", "value": 20}],
        ],
        honorific_folding=True,
    )
    assert idx.get_total("手", "て", prefix_matching=True, honorific_folding=True) == 35


# suffix_total / suffix_matching

def test_suffix_total_excludes_exact_match():
    idx = OccurrenceIndex()
    idx.add("学校", None, 5)
    idx.add("中学校", None, 100)
    assert idx.suffix_total("学校") == 100  # only the longer term; exact handled by get()


def test_suffix_total_head_final_noun_compounds():
    # Table B: card is the semantic head; longer entries ending in it are its hyponyms.
    idx = OccurrenceIndex()
    idx.add("学校", None, 5)
    idx.add("小学校", None, 40)
    idx.add("中学校", None, 30)
    idx.add("高等学校", None, 20)
    idx.add("学校生活", None, 999)  # starts with 学校 (prefix), does NOT end in it -> excluded
    assert idx.suffix_total("学校") == 90


def test_suffix_total_length_gate_excludes_single_kanji():
    # A single kanji is excluded from the BARE suffix path (length >= 2 gate): it would
    # otherwise absorb its whole compound family (語 -> 日本語/英語/…), where the volume/wrongness
    # the gate exists to prevent. Single kanji return only via the tail phrase carve-out.
    idx = OccurrenceIndex()
    idx.add("語", "ご", 5)
    idx.add("日本語", "にほんご", 100)
    idx.add("英語", "えいご", 30)
    assert idx.suffix_total("語") == 0
    assert idx.get_total("語", "ご", suffix_matching=True) == 5   # only the exact count


def test_suffix_total_compound_verb_head_kana_tail_but_kanji_anchored():
    # Table C: 出す ends in kana but contains a kanji -> eligible; the full-string tail
    # keeps the match specific to genuine 〜出す compound verbs.
    idx = OccurrenceIndex()
    idx.add("出す", None, 5)
    idx.add("思い出す", None, 40)
    idx.add("飛び出す", None, 30)
    idx.add("出発", None, 999)  # starts with 出, does NOT end in 出す -> excluded
    assert idx.suffix_total("出す") == 70


def test_suffix_total_requires_kanji_in_expression():
    # Table G: a pure-kana card (a bare grammatical string) would match far too broadly,
    # so the gate excludes it. No suffix credit for する / こと / katakana loanwords.
    idx = OccurrenceIndex()
    idx.add("する", "する", 5)
    idx.add("勉強する", "べんきょうする", 100)
    idx.add("ラーメン", None, 3)
    idx.add("インスタントラーメン", None, 200)
    assert idx.suffix_total("する") == 0
    assert idx.suffix_total("ラーメン") == 0
    # via get_total: only the exact count survives
    assert idx.get_total("する", "する", suffix_matching=True) == 5


def test_suffix_total_includes_supplementary_plane_predecessors():
    # Mirror of the prefix supplementary-plane regression: a term whose char right before
    # the suffix is a supplementary-plane kanji (𠮟, U+20B9F) must still be found. Exercises
    # code-point reversal + the U+10FFFF sentinel over reversed strings.
    idx = OccurrenceIndex()
    idx.add("漢字", None, 5)
    idx.add("𠮟漢字", None, 70)
    assert idx.suffix_total("漢字") == 70


def test_suffix_honorific_no_double_count():
    # BLOCKER guard: honorific_to_count credits 茶の間 from お茶の間, and suffix_total(茶の間)
    # ALSO includes お茶の間 (a strict written suffix). With both flags on it must be counted
    # once (60), not twice (110). Uses a MULTI-CHAR card because single kanji are no longer
    # suffix-eligible. The seen boolean twin can't catch this (OR is idempotent).
    idx = _build_index_from_raw(
        [["茶の間", "freq", 10], ["お茶の間", "freq", 50]],
        honorific_folding=True,
    )
    assert idx.get_total("茶の間", "ちゃのま") == 10
    assert idx.get_total("茶の間", "ちゃのま", suffix_matching=True) == 60          # exact 10 + お茶の間 50
    assert idx.get_total("茶の間", "ちゃのま", honorific_folding=True) == 60        # exact 10 + fold 50
    assert idx.get_total("茶の間", "ちゃのま", suffix_matching=True, honorific_folding=True) == 60  # not 110


def test_suffix_single_kanji_no_collision_with_honorific():
    # A single-kanji card (茶) is not suffix-eligible now, so the bare suffix path contributes
    # nothing and there is no collision to guard, so the honorific fold applies once.
    idx = _build_index_from_raw(
        [["茶", "freq", 10], ["お茶", "freq", 50]],
        honorific_folding=True,
    )
    assert idx.get_total("茶", "ちゃ", suffix_matching=True) == 10   # suffix ineligible -> exact only
    assert idx.get_total("茶", "ちゃ", suffix_matching=True, honorific_folding=True) == 60  # exact 10 + fold 50


def test_suffix_honorific_kana_fold_not_subsumed():
    # A kana-only stripped fold (しゃれ←おしゃれ) is NOT suffix-eligible (no kanji), so the
    # honorific credit must survive when both flags are on. The skip only fires when the
    # suffix path actually subsumes the fold.
    idx = _build_index_from_raw(
        [["しゃれ", "freq", 3], ["おしゃれ", "freq", 20]],
        honorific_folding=True,
    )
    # suffix contributes nothing (しゃれ has no kanji); honorific fold still credits 20
    assert idx.get_total("しゃれ", "しゃれ", suffix_matching=True, honorific_folding=True) == 23


def test_prefix_and_suffix_both_on_additive_disjoint():
    idx = OccurrenceIndex()
    idx.add("中学", "ちゅうがく", 5)
    idx.add("中学生", "ちゅうがくせい", 100)    # prefix: starts with 中学
    idx.add("私立中学", "しりつちゅうがく", 30)  # suffix: ends with 中学
    assert idx.get_total("中学", "ちゅうがく", prefix_matching=True) == 105
    assert idx.get_total("中学", "ちゅうがく", suffix_matching=True) == 35
    assert idx.get_total("中学", "ちゅうがく", prefix_matching=True, suffix_matching=True) == 135


def test_prefix_and_suffix_both_on_reduplicative_double_credits():
    # A term that both starts and ends with the card is credited by BOTH paths when both
    # flags are on. Accepted, and pinned here so the behavior can't silently change.
    idx = OccurrenceIndex()
    idx.add("一歩", "いっぽ", 5)
    idx.add("一歩一歩", "いっぽいっぽ", 40)
    assert idx.get_total("一歩", "いっぽ", prefix_matching=True) == 45
    assert idx.get_total("一歩", "いっぽ", suffix_matching=True) == 45
    assert idx.get_total("一歩", "いっぽ", prefix_matching=True, suffix_matching=True) == 85


def test_suffix_normalize_kana_end_to_end(monkeypatch):
    # A multi-char kanji-bearing card still triggers after katakana->hiragana folding.
    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        return _build_index_from_raw(
            [["中学校", "freq", {"reading": "チュウガッコウ", "value": 100}],
             ["学校", "freq", {"reading": "ガッコウ", "value": 5}]],
            normalize_kana=normalize_kana,
        )

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    count = occurrence_count(["D"], "学校", "ガッコウ", normalize_kana=True, suffix_matching=True)
    assert count == 105


# single-kanji suffix phrase carve-out (mirror of the prefix phrase tests)

def test_suffix_phrase_credits_particle_phrase():
    idx = OccurrenceIndex()
    idx.add("日", "ひ", 5)
    idx.add("母の日", "ははのひ", 12)
    idx.add("子供の日", "こどものひ", 8)
    idx.add("日本語", "にほんご", 100)  # 日 at the HEAD, no preceding particle -> not a tail phrase
    assert idx.get_total("日", "ひ") == 5
    # exact 5 + 母の日 12 + 子供の日 8; 日本語 excluded, and bare single-kanji is gated out
    assert idx.get_total("日", "ひ", suffix_matching=True) == 25
    assert idx.suffix_total("日") == 0


def test_suffix_phrase_reading_validation():
    # Rejects a reading mismatch (日/にち not credited by 母の日/ははのひ); credits the matching
    # homograph but not the mismatched one (敵/かたき ← 目の敵/めのかたき, 敵/てき not). The tail
    # mirror of test_phrase_total_homograph_readings.
    idx = OccurrenceIndex()
    idx.add("母の日", "ははのひ", 12)
    idx.add("目の敵", "めのかたき", 7)
    assert idx.get_total("日", "にち", suffix_matching=True) == 0
    assert idx.get_total("敵", "てき", suffix_matching=True) == 0
    assert idx.get_total("敵", "かたき", suffix_matching=True) == 7


def test_suffix_phrase_requires_particle_and_tail():
    idx = OccurrenceIndex()
    idx.add("の日", "のひ", 4)              # len 2, no head -> fails len>=3
    idx.add("中学校", "ちゅうがっこう", 30)  # 校 preceded by 学 (not a particle)
    assert idx.get_total("日", "ひ", suffix_matching=True) == 0
    assert idx.get_total("校", "こう", suffix_matching=True) == 0


def test_suffix_phrase_kana_only_entry_never_lands_in_kanji_bucket():
    # A ㋕-flagged phrase is keyed under its kana reading, so its effective expression is kana;
    # its last char is not a kanji -> it never enters the suffix phrase bucket.
    idx = _build_index_from_raw([
        ["母の日", "freq", {"reading": "ははのひ",
                            "frequency": {"value": 12, "displayValue": "㋕12"}}],
    ])
    assert idx.get_total("日", "ひ", suffix_matching=True) == 0


def test_suffix_phrase_normalize_kana_end_to_end(monkeypatch):
    # The tail carve-out's reading validation must fold too: a katakana-reading phrase entry
    # still validates a (folded) single-kanji card reading. Mirror of the prefix phrase version.
    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        return _build_index_from_raw(
            [["母の日", "freq", {"reading": "ハハノヒ", "value": 12}]],
            normalize_kana=normalize_kana,
        )

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    count = occurrence_count(["D"], "日", "ヒ", normalize_kana=True, suffix_matching=True)
    assert count == 12


# variant_total / variant_matching

def _kirameku():
    """The motivating index: three written forms of きらめく plus the kana spelling."""
    idx = OccurrenceIndex()
    idx.add("煌く", "きらめく", 40)      # same kanji as 煌めく, different okurigana
    idx.add("燦めく", "きらめく", 25)    # alternate kanji spelling
    idx.add("きらめく", "きらめく", 60)  # kana-only spelling
    return idx


def test_variant_okurigana_difference_credits_shared_kanji_form():
    # The core case: 煌く is neither a prefix nor a suffix of 煌めく, so only the variant rule
    # can bridge them. Same reading + nesting kanji ({煌} <= {煌}) -> credited.
    idx = _kirameku()
    assert idx.get_total("煌めく", "きらめく") == 0
    assert idx.get_total("煌めく", "きらめく", variant_matching=True) == 40


def test_variant_alternate_kanji_spelling_not_credited():
    # 燦めく shares no kanji with 煌く, so the 煌く entry must not reach it (and vice versa).
    idx = _kirameku()
    assert idx.get_total("燦めく", "きらめく", variant_matching=True) == 25  # its own entry only
    idx_no_self = OccurrenceIndex()
    idx_no_self.add("煌く", "きらめく", 40)
    assert idx_no_self.get_total("燦めく", "きらめく", variant_matching=True) == 0


def test_variant_superset_spelling_credits_every_component_form():
    # Hypothetical 煌燦めく: its kanji {煌,燦} is a superset of both cards' skeletons, so it
    # credits BOTH 煌めく and 燦めく. One shared kanji is enough to connect the forms.
    idx = OccurrenceIndex()
    idx.add("煌燦めく", "きらめく", 15)
    assert idx.get_total("煌めく", "きらめく", variant_matching=True) == 15
    assert idx.get_total("燦めく", "きらめく", variant_matching=True) == 15
    # a card with an unrelated kanji still gets nothing
    assert idx.get_total("輝めく", "きらめく", variant_matching=True) == 0


def test_variant_credits_forms_differing_only_in_glyph():
    # 燈/灯 and 搔/掻 are KANJIDIC2 variant glyphs, so the skeletons fold to one kanji and nest.
    idx = OccurrenceIndex()
    idx.add("灯す", "ともす", 8)
    idx.add("搔く", "かく", 12)
    assert idx.get_total("燈す", "ともす") == 0
    assert idx.get_total("燈す", "ともす", variant_matching=True) == 8
    assert idx.get_total("掻く", "かく", variant_matching=True) == 12


def test_variant_glyph_fold_still_requires_the_reading():
    idx = OccurrenceIndex()
    idx.add("灯す", "ひす", 8)
    assert idx.get_total("燈す", "ともす", variant_matching=True) == 0


def test_glyph_fold_degrades_to_nothing_without_the_table(monkeypatch):
    monkeypatch.setattr(dm, "_VARIANTS_FILE", "no_such_table.txt")
    monkeypatch.setattr(dm, "_glyph_canon_map", None)
    assert dm._kanji_skeleton("燈す") == "燈"
    idx = OccurrenceIndex()
    idx.add("灯す", "ともす", 8)
    assert idx.get_total("燈す", "ともす", variant_matching=True) == 0
    monkeypatch.setattr(dm, "_glyph_canon_map", None)  # reload the real table afterwards


def test_variant_never_credits_kana_only_spelling():
    # The measured part: a kana entry has an empty kanji skeleton, so it never participates,
    # that credit remains combine_word_forms' job.
    idx = OccurrenceIndex()
    idx.add("きらめく", "きらめく", 60)
    assert idx.get_total("煌めく", "きらめく", variant_matching=True) == 0
    assert idx.get_total("煌めく", "きらめく", variant_matching=True, combine_word_forms=True) == 60
    # and a kana-only CARD gets no variant credit from the kanji forms
    assert _kirameku().get_total("きらめく", "きらめく", variant_matching=True) == 60  # exact only


def test_variant_rejects_same_reading_homophones_that_merely_share_a_kanji():
    # Why nesting and not intersection: these pairs share exactly one kanji and read alike but
    # are different words. Neither skeleton nests, so no credit crosses.
    idx = OccurrenceIndex()
    idx.add("化学", "かがく", 500)
    idx.add("保障", "ほしょう", 300)
    idx.add("対照", "たいしょう", 200)
    idx.add("市立", "しりつ", 100)
    assert idx.get_total("科学", "かがく", variant_matching=True) == 0
    assert idx.get_total("保証", "ほしょう", variant_matching=True) == 0
    assert idx.get_total("対象", "たいしょう", variant_matching=True) == 0
    assert idx.get_total("私立", "しりつ", variant_matching=True) == 0


def test_variant_okurigana_and_iteration_mark_families():
    # Equal kanji sets after dedup: the okurigana-difference family, plus 々 (not a kanji, so
    # 人々 and 人人 share the skeleton 人).
    idx = OccurrenceIndex()
    idx.add("落ち葉", "おちば", 30)
    idx.add("引っ越し", "ひっこし", 20)
    idx.add("行なう", "おこなう", 10)
    idx.add("子ども", "こども", 50)   # subset: {子} <= {子,供}
    idx.add("人々", "ひとびと", 70)
    assert idx.get_total("落葉", "おちば", variant_matching=True) == 30
    assert idx.get_total("引越し", "ひっこし", variant_matching=True) == 20
    assert idx.get_total("行う", "おこなう", variant_matching=True) == 10
    assert idx.get_total("子供", "こども", variant_matching=True) == 50
    assert idx.get_total("人人", "ひとびと", variant_matching=True) == 70


def test_variant_requires_identical_reading():
    # 煌々/こうこう nests with 煌めく ({煌} <= {煌}) but reads differently, so it is not a
    # variant. The reading is what identifies the word.
    idx = OccurrenceIndex()
    idx.add("煌々", "こうこう", 90)
    assert idx.get_total("煌めく", "きらめく", variant_matching=True) == 0


def test_variant_requires_entry_reading():
    # Entries added without a reading never land in expr_reading_to_count, so they can't be
    # variant candidates (documented limitation: the dict must carry readings).
    idx = OccurrenceIndex()
    idx.add("煌く", None, 40)
    assert idx.variant_total("煌めく", "きらめく") == 0
    assert idx.get_total("煌めく", "きらめく", variant_matching=True) == 0


def test_variant_total_ignores_kana_only_card_expression():
    idx = _kirameku()
    assert idx.variant_total("きらめく", "きらめく") == 0
    assert idx.variant_total("煌めく", "") == 0


def test_variant_prefix_no_double_count():
    # 気持ち is BOTH a strict written prefix of nothing and a variant of 気持
    # (same reading, {気,持} == {気,持}), and 気持 IS a strict prefix of 気持ち, so
    # prefix_total already credits it. With both flags on it must count once, not twice.
    idx = OccurrenceIndex()
    idx.add("気持", "きもち", 5)
    idx.add("気持ち", "きもち", 100)
    assert idx.get_total("気持", "きもち", variant_matching=True) == 105        # exact 5 + variant 100
    assert idx.get_total("気持", "きもち", prefix_matching=True) == 105          # exact 5 + prefix 100
    assert idx.get_total("気持", "きもち", prefix_matching=True, variant_matching=True) == 105  # not 205


def test_variant_suffix_no_double_count():
    # Tail mirror of the guard. Synthetic: a longer entry with an IDENTICAL reading that also
    # ends with the card expression is structurally near-impossible in real data (the extra
    # leading characters would have to be silent), so this pins the guard rather than a real case.
    idx = OccurrenceIndex()
    idx.add("菓子", "かし", 5)
    idx.add("和菓子", "かし", 100)  # ends with 菓子, kanji {和,菓,子} superset of {菓,子}
    assert idx.get_total("菓子", "かし", variant_matching=True) == 105
    assert idx.get_total("菓子", "かし", suffix_matching=True) == 105
    assert idx.get_total("菓子", "かし", suffix_matching=True, variant_matching=True) == 105  # not 205


def test_variant_ineligible_prefix_length_keeps_variant_credit():
    # The prefix guard must reproduce prefix_total's own gate: a 1-char card is below
    # _MIN_PREFIX_LENGTH, so prefix_total contributes nothing and the variant credit must
    # survive even with prefix_matching on.
    idx = OccurrenceIndex()
    idx.add("摑", "つかむ", 5)
    idx.add("摑む", "つかむ", 60)
    assert idx.prefix_total("摑") == 0
    assert idx.get_total("摑", "つかむ", prefix_matching=True, variant_matching=True) == 65


def test_variant_index_built_once_and_lazily():
    idx = _kirameku()
    assert idx._variant_index is None  # untouched until the first variant query
    idx.variant_total("煌めく", "きらめく")
    built = idx._variant_index
    assert built is not None
    idx.variant_total("燦めく", "きらめく")
    assert idx._variant_index is built  # reused, not rebuilt


def test_variant_normalize_kana_end_to_end(monkeypatch):
    # Variant grouping keys on the reading, so it must see folded readings on both sides.
    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        return _build_index_from_raw(
            [["煌く", "freq", {"reading": "キラメク", "value": 40}]],
            normalize_kana=normalize_kana,
        )

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    count = occurrence_count(["D"], "煌めく", "キラメク", normalize_kana=True, variant_matching=True)
    assert count == 40


def test_variant_kana_only_marker_entry_never_participates():
    # A ㋕-flagged entry is re-keyed under its kana reading, so its effective expression has no
    # kanji -> it stays out of the variant index (only combine_word_forms can credit it).
    idx = _build_index_from_raw([
        ["煌く", "freq", {"reading": "きらめく",
                          "frequency": {"value": 40, "displayValue": "㋕40"}}],
    ])
    assert idx.get_total("煌めく", "きらめく", variant_matching=True) == 0
    assert idx.get_total("煌めく", "きらめく", variant_matching=True, combine_word_forms=True) == 40


def test_variant_katakana_expression_never_credits_kanji_card():
    # A katakana-written entry folds to hiragana under kana_normalization but still has no kanji,
    # so it stays out of the variant index. Only combine_word_forms bridges kana to a kanji card.
    idx = _build_index_from_raw(
        [["キラメク", "freq", {"reading": "キラメク", "value": 25}]],
        normalize_kana=True,
    )
    assert idx.get_total("煌めく", "きらめく", variant_matching=True) == 0
    assert idx.get_total("煌めく", "きらめく", variant_matching=True, combine_word_forms=True) == 25


def test_variant_explicit_card_kanji_matches_derived():
    # card_kanji is an optimization hint for the multi-dict path: supplying it must give exactly
    # the total variant_total derives on its own.
    idx = _kirameku()
    derived = idx.variant_total("煌めく", "きらめく")
    assert derived == 40
    assert idx.variant_total("煌めく", "きらめく", card_kanji=dm._kanji_skeleton("煌めく")) == derived


def test_variant_multi_dict_with_kana_normalization(monkeypatch):
    # The combined path folds katakana ONCE in occurrence_count, before any per-dict lookup, and
    # the hoisted card skeleton is derived from that folded expression. Every other kana-folding
    # test uses a single dict, so this is the only cover for that interaction.
    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        return _build_index_from_raw(
            [["煌く", "freq", {"reading": "キラメク", "value": 20}]],
            normalize_kana=normalize_kana,
        )

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    dm.get_combined_occurrence_index.cache_clear()
    try:
        count = occurrence_count(
            ["A", "B"], "煌めく", "キラメク", normalize_kana=True, variant_matching=True
        )
    finally:
        dm.get_combined_occurrence_index.cache_clear()
    assert count == 40  # 20 credited from each of the two dicts


# stem matching (連用形 / さ・み・げ)

def _stem_index(entries):
    ix = OccurrenceIndex()
    for expr, reading, count in entries:
        ix.add(expr, reading, count)
    return ix


def test_stem_credits_ichidan_verb_from_its_masu_stem():
    # The motivating case: a 戒める card and a dict that only lists the 連用形 noun 戒め.
    ix = _stem_index([("戒め", "いましめ", 4)])
    assert ix.get_total("戒める", "いましめる") == 0
    assert ix.get_total("戒める", "いましめる", stem_matching=True) == 4


def test_stem_credits_godan_verb_via_u_row_to_i_row_shift():
    ix = _stem_index([("遊び", "あそび", 7), ("待ち", "まち", 5), ("話し", "はなし", 3),
                      ("泳ぎ", "およぎ", 2)])
    for expr, reading, expected in [("遊ぶ", "あそぶ", 7), ("待つ", "まつ", 5),
                                    ("話す", "はなす", 3), ("泳ぐ", "およぐ", 2)]:
        assert ix.get_total(expr, reading, stem_matching=True) == expected, expr


def test_stem_credits_adjective_nominalizations():
    ix = _stem_index([("強さ", "つよさ", 11), ("痛み", "いたみ", 6), ("寂しげ", "さびしげ", 2)])
    assert ix.get_total("強い", "つよい", stem_matching=True) == 11
    assert ix.get_total("痛い", "いたい", stem_matching=True) == 6
    assert ix.get_total("寂しい", "さびしい", stem_matching=True) == 2


def test_stem_sums_every_adjective_nominalizer_present():
    # さ/み/げ are probed together, so an adjective with more than one derived noun takes all.
    ix = _stem_index([("強さ", "つよさ", 11), ("強み", "つよみ", 3), ("強げ", "つよげ", 1)])
    assert ix.get_total("強い", "つよい", stem_matching=True) == 15


def test_stem_credits_the_adjective_renyoukei():
    # く is the adjective's own 連用形, which dictionaries list as an adverb (早く, 多く).
    assert ("早く", "はやく") in dm._stem_candidates("早い", "はやい")
    ix = _stem_index([("早く", "はやく", 8), ("早く", "さく", 50)])
    assert ix.get_total("早い", "はやい", stem_matching=True) == 8


def test_compound_sweeps_the_adjective_renyoukei():
    ix = _stem_index([("少なくとも", "すくなくとも", 17), ("少なくない", "すくなくない", 2)])
    assert ix.get_total("少ない", "すくない", compound_matching=True) == 19


def test_stem_lets_the_reading_arbitrate_the_conjugation_class():
    # A る-final card yields BOTH the ichidan (drop る) and godan (る->り) candidates; the index
    # decides. 起きる is ichidan so only 起き exists, 走る is godan so only 走り does, and the
    # wrong-class candidate contributes nothing rather than needing a dictionary to rule it out.
    ix = _stem_index([("起き", "おき", 9), ("走り", "はしり", 4)])
    assert ix.get_total("起きる", "おきる", stem_matching=True) == 9
    assert ix.get_total("走る", "はしる", stem_matching=True) == 4


def test_stem_requires_the_reading_to_match_the_written_stem():
    # The exact (expression, reading) probe is the whole safety argument: a homograph stem read
    # differently is a different word and earns nothing.
    ix = _stem_index([("戒め", "かいめ", 4)])
    assert ix.get_total("戒める", "いましめる", stem_matching=True) == 0


def test_stem_is_forward_only_and_never_credits_the_dictionary_form():
    # Deliberate: crediting a 連用形 card from its (far commoner) dictionary form inverts the
    # priority ordering, since 無げ would inherit 無い's count. prefix_matching still covers that
    # direction for anyone who wants it.
    ix = _stem_index([("戒める", "いましめる", 40)])
    assert ix.get_total("戒め", "いましめ", stem_matching=True) == 0
    assert ix.get_total("戒め", "いましめ", stem_matching=True, prefix_matching=True) == 40


def test_stem_rejects_kana_only_cards_where_the_reading_validates_nothing():
    # expression == reading collapses the pair probe into a single kana lookup, so それる would
    # take the pronoun それ's entire count. Measured, this gate drops ~49% of the rule's raw
    # credit and no legitimate matches.
    ix = _stem_index([("それ", "それ", 99), ("ほう", "ほう", 50), ("のり", "のり", 30)])
    assert ix.get_total("それる", "それる", stem_matching=True) == 0
    assert ix.get_total("ほうる", "ほうる", stem_matching=True) == 0
    assert ix.get_total("のる", "のる", stem_matching=True) == 0


def test_stem_requires_the_okurigana_invariant():
    # The edit is only valid when expression and reading end in the SAME kana, which is what
    # makes the tail okurigana. A kanji-final card can never qualify.
    ix = _stem_index([("学", "がく", 5)])
    assert ix.get_total("学校", "がっこう", stem_matching=True) == 0
    assert _stem_candidates("学校", "がっこう") == []


def test_stem_drops_single_character_candidates():
    # 見る/見 is correct but rare, while the bare-kanji nouns the wrong class produces (神る->神)
    # are common and large. The length gate is the same call prefix_total/_suffix_eligible make.
    ix = _stem_index([("見", "み", 9), ("神", "かみ", 94)])
    assert ix.get_total("見る", "みる", stem_matching=True) == 0
    assert ix.get_total("神る", "かみる", stem_matching=True) == 0


def test_stem_candidates_survive_empty_and_absent_readings():
    assert _stem_candidates("", "") == []
    assert _stem_candidates("戒める", "") == []
    assert _stem_candidates("", "いましめる") == []
    ix = _stem_index([("戒め", "いましめ", 4)])
    assert ix.get_total("", "", stem_matching=True) == 0


def test_stem_credits_kana_stem_through_combine_word_forms():
    # A ㋕ entry is re-keyed under its reading, so the stem's kana form lives in expr_to_count
    # only. That is stem_total's second term, and it is gated on combine_word_forms like every
    # other reading-side term in get_total.
    ix = OccurrenceIndex()
    ix.add("いましめ", "いましめ", 50)   # as _build_index_from_raw re-keys a ㋕ entry
    assert ix.get_total("戒める", "いましめる", stem_matching=True) == 0
    assert ix.get_total("戒める", "いましめる", stem_matching=True,
                        combine_word_forms=True) == 50


def test_stem_does_not_double_count_with_prefix_suffix_or_variant():
    # Every candidate either shortens the card or replaces its last character, so no other rule
    # can reach it, so this is the one rule in the file that needs no dedup guard. Pinning it here
    # means a future widening that breaks the property fails loudly.
    ix = _stem_index([
        ("戒め", "いましめ", 4),          # the stem itself
        ("戒めるもの", "いましめるもの", 8),  # prefix candidate for 戒める
        ("自戒める", "じいましめる", 3),      # suffix candidate for 戒める
        ("誡める", "いましめる", 5),          # variant candidate (same reading, nesting kanji)
    ])
    base = ix.get_total("戒める", "いましめる", prefix_matching=True, suffix_matching=True,
                        variant_matching=True)
    withstem = ix.get_total("戒める", "いましめる", prefix_matching=True, suffix_matching=True,
                            variant_matching=True, stem_matching=True)
    assert withstem - base == 4   # exactly the stem, counted once


def test_stem_total_builds_no_lazy_view():
    # Unlike every other rule here, stem matching reads only the two eager maps. A stem query
    # must not materialize the prefix / suffix / variant views.
    ix = _stem_index([("戒め", "いましめ", 4)])
    assert ix.get_total("戒める", "いましめる", stem_matching=True) == 4
    assert ix._prefix_exprs is None
    assert ix._suffix_revs is None
    assert ix._variant_index is None


# compound matching (entries built ON the stem)

def test_compound_credits_a_verb_from_a_compound_built_on_its_stem():
    # The motivating case: a 奮う card and a dict that only lists 奮い立つ. No other rule sees it,
    # since 奮い立つ neither starts nor ends with 奮う and its reading is not the card's.
    ix = _stem_index([("奮い立つ", "ふるいたつ", 16)])
    assert ix.get_total("奮う", "ふるう", prefix_matching=True, suffix_matching=True,
                        variant_matching=True, stem_matching=True) == 0
    assert ix.get_total("奮う", "ふるう", compound_matching=True) == 16


def test_compound_sums_every_compound_on_a_godan_stem():
    ix = _stem_index([("取り消す", "とりけす", 5), ("取り扱い", "とりあつかい", 7),
                      ("取り分け", "とりわけ", 3)])
    assert ix.get_total("取る", "とる", compound_matching=True) == 15


def test_compound_reaches_ichidan_compounds_through_the_truncated_stem():
    # The ichidan candidate is what covers 受ける -> 受け入れる and 食べる -> 食べ物. Dropping it
    # would cost a third of the rule.
    ix = _stem_index([("受け入れる", "うけいれる", 122), ("食べ物", "たべもの", 40)])
    assert ix.get_total("受ける", "うける", compound_matching=True) == 122
    assert ix.get_total("食べる", "たべる", compound_matching=True) == 40


def test_compound_requires_the_reading_to_match_the_stem():
    # Both sides must match, or a homograph card takes the other word's compounds: 抱く/だく owns
    # 抱きしめる, 抱く/いだく does not.
    ix = _stem_index([("抱きしめる", "だきしめる", 848)])
    assert ix.get_total("抱く", "だく", compound_matching=True) == 848
    assert ix.get_total("抱く", "いだく", compound_matching=True) == 0


def test_compound_ignores_kana_keyed_entries():
    # _build_index_from_raw re-keys every ㋕ entry under its reading, so the pair map is full of
    # (kana, kana) entries. A kana pair validates nothing, and crediting one here would let a
    # 取る card take every とり… kana entry in the dict.
    ix = OccurrenceIndex()
    ix.add("とりあえず", "とりあえず", 90)   # as _build_index_from_raw re-keys a ㋕ entry
    assert ix.get_total("取る", "とる", compound_matching=True) == 0
    assert ix.get_total("取る", "とる", compound_matching=True, combine_word_forms=True) == 0


def test_compound_covers_the_exact_stem_only_while_stem_matching_is_off():
    # stem_matching is a dedup guard, not a widening knob: with it off nothing else credits the
    # exact stem, so this rule does; with it on, stem_total owns it and the total is unchanged.
    ix = _stem_index([("取り", "とり", 20), ("取り消す", "とりけす", 5)])
    assert ix.get_total("取る", "とる", compound_matching=True) == 25
    assert ix.get_total("取る", "とる", stem_matching=True) == 20
    assert ix.get_total("取る", "とる", compound_matching=True, stem_matching=True) == 25


def test_compound_keeps_a_stem_spelled_entry_that_reads_longer():
    # The dedup is on the PAIR. 戒め/いましめる is spelled like the stem but read past it, so
    # stem_total's exact probe never sees it and it belongs to this rule.
    ix = _stem_index([("戒め", "いましめる", 6)])
    assert ix.get_total("戒める", "いましめる", stem_matching=True) == 0
    assert ix.get_total("戒める", "いましめる", stem_matching=True, compound_matching=True) == 6


def test_compound_concedes_entries_starting_with_the_card_to_prefix_matching():
    # Those belong to prefix_total, and the concession is unconditional: crediting them with
    # prefix_matching off would make this rule a silent superset of prefix matching, since the
    # ichidan candidate 食べ swallows every 食べる… entry.
    ix = _stem_index([("食べるもの", "たべるもの", 30), ("食べ物", "たべもの", 40)])
    assert ix.get_total("食べる", "たべる", compound_matching=True) == 40
    assert ix.get_total("食べる", "たべる", compound_matching=True, prefix_matching=True) == 70


def test_compound_does_not_double_count_an_okurigana_variant():
    # The ichidan candidate reading is the card's minus its last kana, so a variant carrying the
    # card's exact reading sits inside the swept range. Measured on real dicts: 222 counts, all
    # okurigana pairs like this one.
    ix = _stem_index([("立ち止まる", "たちどまる", 109)])
    withvariant = ix.get_total("立ち止る", "たちどまる", variant_matching=True)
    assert withvariant == 109
    assert ix.get_total("立ち止る", "たちどまる", variant_matching=True,
                        compound_matching=True) == withvariant
    # Without variant_matching nothing else credits it, so the sweep does.
    assert ix.get_total("立ち止る", "たちどまる", compound_matching=True) == 109


def test_compound_counts_a_nested_candidate_range_once():
    # A る-final card yields both 食べ and 食べり, and the godan range nests inside the ichidan
    # one. Summing both would count 食べりんご twice.
    ix = _stem_index([("食べりんご", "たべりんご", 11)])
    assert _stem_candidates("食べる", "たべる") == [("食べ", "たべ"), ("食べり", "たべり")]
    assert ix.get_total("食べる", "たべる", compound_matching=True) == 11


def test_compound_still_sweeps_the_godan_stem_when_the_ichidan_one_is_too_short():
    # 見る drops its ichidan candidate at _MIN_STEM_LENGTH, so the nesting skip must be dynamic
    # rather than "sweep the shortest and stop".
    ix = _stem_index([("見り所", "みりどころ", 4)])
    assert ix.get_total("見る", "みる", compound_matching=True) == 4


def test_compound_rejects_kana_only_cards():
    # Same gate as stem matching: expression == reading yields no candidates at all, which is
    # what keeps a それる card off every それ… entry.
    ix = _stem_index([("それなり", "それなり", 77)])
    assert ix.get_total("それる", "それる", compound_matching=True) == 0


def test_compound_credits_adjective_nominalization_compounds():
    ix = _stem_index([("強さ比べ", "つよさくらべ", 6), ("痛み止め", "いたみどめ", 9)])
    assert ix.get_total("強い", "つよい", compound_matching=True) == 6
    assert ix.get_total("痛い", "いたい", compound_matching=True) == 9


def test_compound_builds_only_its_own_lazy_view():
    ix = _stem_index([("奮い立つ", "ふるいたつ", 16)])
    assert ix.get_total("奮う", "ふるう", compound_matching=True) == 16
    assert ix._prefix_exprs is None
    assert ix._suffix_revs is None
    assert ix._variant_index is None
    assert ix._stem_compound_keys is not None


def test_compound_view_is_not_built_when_the_flag_is_off():
    ix = _stem_index([("奮い立つ", "ふるいたつ", 16)])
    ix.get_total("奮う", "ふるう", prefix_matching=True, suffix_matching=True,
                 variant_matching=True, stem_matching=True)
    assert ix._stem_compound_keys is None
    assert ix._stem_tail_keys is None


# tail compounds (compound_matching over entries ENDING in the stem)

def test_tail_compound_credits_a_verb_from_a_compound_ending_in_its_stem():
    ix = _stem_index([("時間稼ぎ", "じかんかせぎ", 14), ("荒稼ぎ", "あらかせぎ", 2)])
    assert ix.get_total("稼ぐ", "かせぐ") == 0
    assert ix.get_total("稼ぐ", "かせぐ", compound_matching=True) == 16


def test_tail_compound_allows_rendaku_on_the_stem():
    ix = _stem_index([("手触り", "てざわり", 16), ("言葉責め", "ことばぜめ", 8)])
    assert ix.get_total("触る", "さわる", compound_matching=True) == 16
    assert ix.get_total("責める", "せめる", compound_matching=True) == 8


def test_tail_compound_voices_only_a_kanji_written_first_kana():
    # くっ付き is written with its く, so ぐっつき cannot be the same stem.
    ix = _stem_index([("ベタくっ付き", "べたぐっつき", 5)])
    assert ix.get_total("くっ付く", "くっつく", compound_matching=True) == 0


def test_tail_compound_requires_the_reading_to_match_the_stem():
    ix = _stem_index([("時間稼ぎ", "じかんかせき", 14)])
    assert ix.get_total("稼ぐ", "かせぐ", compound_matching=True) == 0


def test_tail_compound_needs_something_before_the_stem():
    # The bare stem is stem_total's (or the prefix sweep's), not a tail compound.
    ix = _stem_index([("稼ぎ", "かせぎ", 9)])
    assert ix.stem_tail_compound_total("稼ぐ", "かせぐ") == 0


def test_tail_compound_leaves_entries_the_prefix_sweep_owns():
    # 泣き泣き both starts and ends with 泣き. The prefix sweep credits it, the tail rule must not.
    ix = _stem_index([("泣き泣き", "なきなき", 3)])
    assert ix.stem_tail_compound_total("泣く", "なく") == 0
    assert ix.get_total("泣く", "なく", compound_matching=True) == 3


def test_tail_compound_leaves_entries_starting_with_the_card_to_prefix_matching():
    ix = _stem_index([("取るに足りない取り", "とるにたりないとり", 4)])
    assert ix.stem_tail_compound_total("取る", "とる") == 0


def test_tail_compound_reaches_adjective_forms():
    ix = _stem_index([("注意深く", "ちゅういぶかく", 12), ("力強さ", "ちからづよさ", 3)])
    assert ix.get_total("深い", "ふかい", compound_matching=True) == 12
    assert ix.get_total("強い", "つよい", compound_matching=True) == 3


def test_tail_compound_ignores_kana_keyed_entries():
    ix = _stem_index([("ときかせぎ", "ときかせぎ", 40)])
    assert ix.get_total("稼ぐ", "かせぐ", compound_matching=True) == 0


# conjugated-form tails (suffix_matching over 未然形 + ず/ぬ, and the て-form)

def test_negative_forms_cover_godan_and_both_readings_of_ru_verbs():
    assert dm._negative_forms("思う", "おもう") == [("思わず", "おもわず"), ("思わぬ", "おもわぬ")]
    assert dm._negative_forms("拘わる", "かかわる") == [
        ("拘わず", "かかわず"), ("拘わぬ", "かかわぬ"),       # ichidan reading
        ("拘わらず", "かかわらず"), ("拘わらぬ", "かかわらぬ"),  # godan reading
    ]


def test_negative_forms_reject_what_stem_candidates_reject():
    assert dm._negative_forms("する", "する") == []           # expression == reading
    assert dm._negative_forms("学校", "がっこう") == []       # kanji-final
    assert dm._negative_forms("強い", "つよい") == []         # adjectives are not handled
    assert dm._negative_forms("", "") == []


def test_negative_suffix_credits_a_verb_from_a_phrase_ending_in_its_negative():
    # The motivating case: no dict lists 拘わらず alone, only phrases ending in it.
    ix = _stem_index([("にも拘わらず", "にもかかわらず", 10), ("それにも拘わらず", "それにもかかわらず", 2)])
    assert ix.get_total("拘わる", "かかわる") == 0
    assert ix.get_total("拘わる", "かかわる", suffix_matching=True) == 12


def test_negative_suffix_credits_the_form_itself_and_the_nu_negative():
    ix = _stem_index([("思わず", "おもわず", 5), ("思わぬ", "おもわぬ", 3), ("絶えず", "たえず", 2)])
    assert ix.get_total("思う", "おもう", suffix_matching=True) == 8
    assert ix.get_total("絶える", "たえる", suffix_matching=True) == 2   # ichidan


def test_negative_suffix_requires_the_reading_tail():
    # Same written tail, other reading: 拘る/こだわる must not take にも拘らず, nor 入る/はいる 水入らず.
    ix = _stem_index([("にも拘らず", "にもかかわらず", 7), ("水入らず", "みずいらず", 2)])
    assert ix.get_total("拘る", "こだわる", suffix_matching=True) == 0
    assert ix.get_total("拘る", "かかわる", suffix_matching=True) == 7
    assert ix.get_total("入る", "はいる", suffix_matching=True) == 0
    assert ix.get_total("入る", "いる", suffix_matching=True) == 2


def test_negative_suffix_skips_entries_starting_with_the_card():
    ix = _stem_index([("変わる変わらず", "かわるかわらず", 4)])
    assert ix.get_total("変わる", "かわる", suffix_matching=True) == 0
    assert ix.get_total("変わる", "かわる", suffix_matching=True, prefix_matching=True) == 4


def test_negative_suffix_concedes_the_compound_sweep():
    # 拘わらず sits inside the ichidan sweep 拘わ/かかわ, so with both rules on it counts once.
    ix = _stem_index([("拘わらず", "かかわらず", 6)])
    assert ix.get_total("拘わる", "かかわる", suffix_matching=True) == 6
    assert ix.get_total("拘わる", "かかわる", compound_matching=True) == 6
    assert ix.get_total("拘わる", "かかわる", suffix_matching=True, compound_matching=True) == 6


def test_negative_suffix_is_gated_like_suffix_total():
    ix = _stem_index([("見ず知らず", "みずしらず", 3), ("しらず", "しらず", 9)])
    assert ix.get_total("知る", "しる", suffix_matching=True) == 3
    assert ix.get_total("しる", "しる", suffix_matching=True) == 0   # kana card


def test_te_forms_cover_onbin_and_both_readings_of_ru_verbs():
    assert dm._te_forms("急ぐ", "いそぐ") == [("急いで", "いそいで")]
    assert dm._te_forms("沿う", "そう") == [("沿って", "そって")]
    assert dm._te_forms("及ぶ", "およぶ") == [("及んで", "およんで")]
    assert dm._te_forms("除く", "のぞく") == [("除いて", "のぞいて")]
    assert dm._te_forms("増す", "ます") == [("増して", "まして")]
    assert dm._te_forms("限る", "かぎる") == [("限て", "かぎて"), ("限って", "かぎって")]
    assert dm._te_forms("行く", "いく") == [("行いて", "いいて"), ("行って", "いって")]


def test_te_forms_reject_what_negative_forms_reject():
    assert dm._te_forms("する", "する") == []
    assert dm._te_forms("学校", "がっこう") == []
    assert dm._te_forms("強い", "つよい") == []


def test_te_suffix_credits_a_verb_from_phrases_ending_in_its_te_form():
    ix = _stem_index([("急いで", "いそいで", 87), ("に沿って", "にそって", 21),
                      ("この期に及んで", "このごにおよんで", 21), ("行って", "いって", 4)])
    assert ix.get_total("急ぐ", "いそぐ") == 0
    assert ix.get_total("急ぐ", "いそぐ", suffix_matching=True) == 87
    assert ix.get_total("沿う", "そう", suffix_matching=True) == 21
    assert ix.get_total("及ぶ", "およぶ", suffix_matching=True) == 21
    assert ix.get_total("行く", "いく", suffix_matching=True) == 4


def test_te_suffix_requires_the_reading_tail():
    ix = _stem_index([("に沿って", "にぞって", 21)])
    assert ix.get_total("沿う", "そう", suffix_matching=True) == 0


def test_te_suffix_concedes_ichidan_forms_to_the_compound_sweep():
    # 改めて starts with the ichidan stem 改め, so with compound matching on it counts once.
    ix = _stem_index([("改めて", "あらためて", 30)])
    assert ix.get_total("改める", "あらためる", suffix_matching=True) == 30
    assert ix.get_total("改める", "あらためる", suffix_matching=True, compound_matching=True) == 30


# CombinedOccurrenceIndex memo eviction

def test_combined_index_evicts_oldest_when_cap_reached(monkeypatch):
    monkeypatch.setattr(dm, "_COMBINED_MEMO_CAP", 2)

    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        return OccurrenceIndex()  # every lookup totals to 0; we only test eviction

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)

    ci = CombinedOccurrenceIndex(["D1", "D2"])
    ci.total("x", "rx")
    ci.total("y", "ry")
    ci.total("z", "rz")  # cap reached -> oldest ("x","rx") evicted

    # The memo has its own dict: expr_reading_to_count holds real merged dictionary data, so
    # evicting from it would drop entries the fold can never rebuild.
    assert ("z", "rz") in ci._memo
    assert ("x", "rx") not in ci._memo
    assert len(ci._memo) == 2


def test_combined_index_memo_resets_when_query_flags_change(monkeypatch):
    # The merged index is now shared across query-flag combinations (they are no longer in
    # the lru_cache key), so the per-card memo, whose values DO depend on them, must not
    # serve one combination's totals to another.
    index = OccurrenceIndex()
    index.add("学校", "がっこう", 3)
    index.add("小学校", "しょうがっこう", 10)
    monkeypatch.setattr(dm, "get_occurrence_index", lambda *a: index)

    ci = CombinedOccurrenceIndex(["D1"])
    assert ci.total("学校", "がっこう") == 3                          # exact only
    assert ci.total("学校", "がっこう", suffix_matching=True) == 13    # + 小学校
    assert ci.total("学校", "がっこう") == 3                          # and back again


# merged-index equivalence (drift guard)
#
# CombinedOccurrenceIndex folds N dictionaries into ONE index instead of summing N per-dict
# get_total calls. That is a pure optimization: the merged answer must equal the per-dict sum
# EXACTLY, for every flag combination. These tests make that a guarantee rather than a hope.
# the rest of the suite exercises the base OccurrenceIndex and would not notice a bad fold.

# Four overlapping dictionaries, each carrying an entry shape that stresses part of the fold:
#   A/B share 茶 with DIFFERENT readings -> get()'s per-dict reading-mismatch fallback, the one
#                                           term that does not collapse into a merged map
#   お茶 in A, かず-style strips in C      -> _honorific_fold_allowed reads the DICT's own vocab,
#                                           so per-dict honorific maps must be summed, never
#                                           rebuilt from the merged vocabulary
#   煌く / 煌めく / 煌燦めく split         -> variant grouping by identical reading
#   灯す                                  -> the glyph-variant fold (probed as 燈す)
#   手を貸す / 母の日                     -> the single-kanji head/tail phrase carve-outs
#   屯する / 察する                       -> the single-kanji suru carve-out (plain + sokuon)
#   ㋕-marked entry                       -> re-keyed under its reading (combine_word_forms)
#   戒め / 強さ / 遊び split               -> stem matching (ichidan, adjective, godan), each in a
#                                           DIFFERENT dict from the card form the probe uses
#   それ (kana, expr == reading)          -> the stem distinctness gate: a それる card must not
#                                           reach it, or the pair probe validates nothing
#   にも拘わらず / 拘わらず / にも拘らず     -> negative-form tails: a phrase hit, the exact form
#                                           the compound sweep also reaches, a reading miss
#   早く / 早くも                         -> the adjective 連用形, exact and as a compound
#   時間稼ぎ / 手触り / 泣き泣き           -> tail compounds: plain, rendaku, and one the prefix
#                                           sweep owns
#   に沿って / 改めて                     -> て-form tails: a phrase hit, and an ichidan form the
#                                           compound sweep also reaches
_EQUIV_DICTS = {
    "A": [
        ["茶", "freq", {"reading": "ちゃ", "value": 10}],
        # A SECOND reading for 茶 inside one dictionary, so that dict's expr_to_count (15)
        # exceeds either pair count. That gap is exactly what _base_total's diff term carries;
        # without a multi-reading expression anywhere, a fold that dropped the term would still
        # agree with the per-dict sum and the guard would pass while broken.
        ["茶", "freq", {"reading": "さ", "value": 5}],
        ["お茶", "freq", {"reading": "おちゃ", "value": 7}],
        ["煌く", "freq", {"reading": "きらめく", "value": 40}],
        ["手を貸す", "freq", {"reading": "てをかす", "value": 12}],
        ["中学校", "freq", {"reading": "ちゅうがっこう", "value": 100}],
        # Kana-only honorific strip that A alone must NOT fold (A has never heard of かず) but
        # the MERGED vocabulary would happily admit, because B below knows かず. This pair is
        # what makes the broad equivalence check sensitive to a honorific fold rebuilt from
        # merged vocabulary rather than summed per dict.
        ["おかず", "freq", {"reading": "おかず", "value": 50}],
        ["戒め", "freq", {"reading": "いましめ", "value": 4}],      # ichidan stem of 戒める
        ["それ", "freq", {"reading": "それ", "value": 99}],         # kana: stem gate must exclude
        ["奮い立つ", "freq", {"reading": "ふるいたつ", "value": 16}],  # compound on 奮う's stem
        ["にも拘わらず", "freq", {"reading": "にもかかわらず", "value": 13}],
    ],
    "B": [
        ["茶", "freq", {"reading": "さ", "value": 3}],           # same expr, other reading
        ["かず", "freq", {"reading": "かず", "value": 1}],        # see おかず in A
        ["茶碗", "freq", {"reading": "ちゃわん", "value": 21}],    # prefix candidate for 茶
        ["煌めく", "freq", {"reading": "きらめく", "value": 5}],
        ["母の日", "freq", {"reading": "ははのひ", "value": 9}],
        ["屯する", "freq", {"reading": "たむろする", "value": 17}],
        ["察する", "freq", {"reading": "さっする", "value": 23}],   # sokuon branch
        ["学校", "freq", {"reading": "がっこう", "value": 6}],
        ["強さ", "freq", {"reading": "つよさ", "value": 11}],       # adjective stem of 強い
        ["いましめ", "freq", {"reading": "いましめ", "value": 50,
                              "displayValue": "50㋕"}],             # kana stem: combine_word_forms
        ["奮うこと", "freq", {"reading": "ふるうこと", "value": 2}],   # compound must concede this
        ["拘わらず", "freq", {"reading": "かかわらず", "value": 4}],   # compound sweep reaches it too
    ],
    "C": [
        ["ぎりぎり", "freq", {"reading": "ぎりぎり", "value": 8, "displayValue": "8㋕"}],
        ["お金", "freq", {"reading": "おかね", "value": 15}],     # kanji strip, no bare 金 here
        ["茶", "freq", {"reading": "ちゃ", "value": 4}],          # expr+reading also present in A
        ["日", "freq", {"reading": "ひ", "value": 2}],
        ["屯", "freq", {"reading": "たむろ", "value": 6}],        # bare head, other dict than 屯する
        ["遊び", "freq", {"reading": "あそび", "value": 7}],        # godan stem of 遊ぶ
        # The exact stem, in a different dict from the compound above.
        ["奮い", "freq", {"reading": "ふるい", "value": 3}],
        ["にも拘らず", "freq", {"reading": "にもかかわらず", "value": 7}],  # 拘る/こだわる must miss
        ["早く", "freq", {"reading": "はやく", "value": 6}],        # adjective 連用形 of 早い
        ["時間稼ぎ", "freq", {"reading": "じかんかせぎ", "value": 14}],  # tail compound on 稼ぎ
        ["に沿って", "freq", {"reading": "にそって", "value": 21}],      # て-form tail
    ],
    "D": [
        ["茶", "freq", {"value": 33}],                           # no reading at all
        ["手", "freq", {"reading": "て", "value": 1}],
        ["煌燦めく", "freq", {"reading": "きらめく", "value": 2}],
        ["灯す", "freq", {"reading": "ともす", "value": 8}],       # glyph variant of 燈す
        # Okurigana variant of the 立ち止る probe: compound and variant must not both credit it.
        ["立ち止まる", "freq", {"reading": "たちどまる", "value": 109}],
        ["早くも", "freq", {"reading": "はやくも", "value": 3}],      # compound on 早く
        ["手触り", "freq", {"reading": "てざわり", "value": 16}],     # tail compound, rendaku
        ["泣き泣き", "freq", {"reading": "なきなき", "value": 3}],    # prefix sweep owns it
        ["改めて", "freq", {"reading": "あらためて", "value": 30}],   # ichidan て-form, in the sweep
    ],
}

_EQUIV_PROBES = [
    ("茶", "ちゃ"), ("茶", "さ"), ("茶", "ばんちゃ"),    # hit, other dict's reading, unknown
    ("お茶", "おちゃ"), ("金", "かね"), ("茶碗", "ちゃわん"),
    ("煌めく", "きらめく"), ("煌く", "きらめく"), ("煌燦めく", "きらめく"),
    ("燈す", "ともす"),                                    # glyph-variant fold
    ("手", "て"), ("日", "ひ"), ("学校", "がっこう"), ("中学校", "ちゅうがっこう"),
    ("屯", "たむろ"), ("屯", "とん"), ("察", "さつ"),      # suru carve-out: hit, reading miss, sokuon
    ("ぎりぎり", "ぎりぎり"), ("ギリギリ", "ギリギリ"),
    ("かず", "かず"), ("おかず", "おかず"),                # cross-dict honorific fold gate
    ("戒める", "いましめる"), ("戒め", "いましめ"),        # stem: forward hit, and no reverse
    ("遊ぶ", "あそぶ"), ("強い", "つよい"), ("痛い", "いたい"),  # godan, adjective, adjective miss
    ("早い", "はやい"),                                    # adjective 連用形, exact and compound
    ("稼ぐ", "かせぐ"), ("触る", "さわる"), ("泣く", "なく"),  # tail compounds
    ("沿う", "そう"), ("改める", "あらためる"),                # て-form tails
    ("それる", "それる"), ("のる", "のる"),                # kana cards: distinctness / length gates
    ("奮う", "ふるう"), ("奮い", "ふるい"),                # compound: forward hit, and no reverse
    ("立ち止る", "たちどまる"),                            # compound / variant dedup
    ("拘わる", "かかわる"), ("拘る", "かかわる"), ("拘る", "こだわる"),  # negative tails
    ("存在しない", "そんざいしない"), ("", ""),            # absent everywhere, empty
]

_FLAG_NAMES = ("combine_word_forms", "prefix_matching", "suffix_matching",
               "variant_matching", "stem_matching", "compound_matching", "honorific_folding")


def _all_flag_combos():
    for bits in itertools.product((False, True), repeat=len(_FLAG_NAMES)):
        yield dict(zip(_FLAG_NAMES, bits))


def _split_flags(flags):
    """(build-time kwargs, query-time kwargs). honorific_folding is a BUILD flag, since
    honorific_to_count only exists when the per-dict indexes were built with it, while the
    other six are passed per lookup so one merged index serves every combination."""
    return (
        {"honorific_folding": flags["honorific_folding"]},
        {k: v for k, v in flags.items() if k != "honorific_folding"},
    )


def _assert_merged_matches_per_dict(names, raw_by_name, normalize_kana):
    """For every flag combination, the merged total must equal the per-dict sum."""
    for flags in _all_flag_combos():
        build_flags, query_flags = _split_flags(flags)
        merged = CombinedOccurrenceIndex(list(names), normalize_kana=normalize_kana,
                                         **build_flags)
        per_dict = [
            _build_index_from_raw(raw_by_name[n], normalize_kana=normalize_kana,
                                  honorific_folding=flags["honorific_folding"])
            for n in names
        ]
        for expression, reading in _EQUIV_PROBES:
            expr, read = expression, reading
            if normalize_kana:  # occurrence_count folds before either path sees the strings
                expr, read = dm.to_hiragana(expr), dm.to_hiragana(read)
            card_kanji = (dm._kanji_skeleton(expr)
                          if flags["variant_matching"] or flags["compound_matching"] else None)
            expected = sum(ix.get_total(expr, read, card_kanji=card_kanji, **flags)
                           for ix in per_dict)
            assert merged.total(expr, read, **query_flags) == expected, (
                f"merged != per-dict sum for {expression!r}/{reading!r} "
                f"normalize_kana={normalize_kana} flags={flags}"
            )


@pytest.fixture
def equiv_dicts(monkeypatch):
    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        return _build_index_from_raw(_EQUIV_DICTS[name], normalize_kana=normalize_kana,
                                     honorific_folding=honorific_folding)

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    return _EQUIV_DICTS


@pytest.mark.parametrize("normalize_kana", [False, True])
def test_merged_index_equals_per_dict_sum(equiv_dicts, normalize_kana):
    _assert_merged_matches_per_dict(sorted(equiv_dicts), equiv_dicts, normalize_kana)


def test_merged_index_equals_per_dict_sum_for_every_subset(equiv_dicts):
    """Pairs and triples too, since a fold bug can hide behind one particular dict combination."""
    names = sorted(equiv_dicts)
    for size in (2, 3):
        for subset in itertools.combinations(names, size):
            _assert_merged_matches_per_dict(subset, equiv_dicts, normalize_kana=False)


def test_merged_honorific_respects_each_dicts_own_vocabulary_gate(monkeypatch):
    """The sharpest fold trap, pinned on its own.

    _honorific_fold_allowed lets a KANA-only strip through only when that dictionary itself
    knows the stripped form. Here おかず lives in X and かず only in Y: per-dict, X may not fold
    (X has never heard of かず) and Y has no おかず to fold, so かず earns nothing extra.
    Rebuilding the honorific map from the MERGED vocabulary would wrongly credit it 50."""
    raw = {
        "X": [["おかず", "freq", {"reading": "おかず", "value": 50}]],
        "Y": [["かず", "freq", {"reading": "かず", "value": 1}]],
    }

    def fake_get_occurrence_index(name, normalize_kana, honorific_folding):
        return _build_index_from_raw(raw[name], normalize_kana=normalize_kana,
                                     honorific_folding=honorific_folding)

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)

    merged = CombinedOccurrenceIndex(["X", "Y"], honorific_folding=True)
    assert merged.total("かず", "かず") == 1  # おかず's 50 must NOT fold down into it
    _assert_merged_matches_per_dict(["X", "Y"], raw, normalize_kana=False)


@pytest.mark.skipif(not dm.get_all_dict_names(),
                    reason="no dictionaries installed in user_files/")
def test_merged_index_equals_per_dict_sum_on_real_dicts():
    """Opt-in: the same guarantee against whatever real dictionaries are installed, whose entry
    shapes are far messier than any fixture. Skipped in a clean checkout (user_files/ is
    gitignored)."""
    names = dm.get_all_dict_names()[:4]
    if len(names) < 2:
        pytest.skip("need at least two dictionaries to exercise the fold")

    probes = list(dm.get_occurrence_index(names[-1], False, False).expr_reading_to_count)[:150]

    for flags in _all_flag_combos():
        build_flags, query_flags = _split_flags(flags)
        merged = CombinedOccurrenceIndex(names, **build_flags)
        per_dict = [dm.get_occurrence_index(n, False, flags["honorific_folding"]) for n in names]
        for expr, read in probes:
            card_kanji = (dm._kanji_skeleton(expr)
                          if flags["variant_matching"] or flags["compound_matching"] else None)
            expected = sum(ix.get_total(expr, read, card_kanji=card_kanji, **flags)
                           for ix in per_dict)
            assert merged.total(expr, read, **query_flags) == expected, (expr, read, flags)
