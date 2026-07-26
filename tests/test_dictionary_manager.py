import dictionary_manager as dm
from dictionary_manager import (
    CombinedOccurrenceIndex,
    OccurrenceIndex,
    _build_index_from_raw,
    expand_dict_names,
    occurrence_count,
)


# --- expand_dict_names ------------------------------------------------------

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


# --- OccurrenceIndex.get_total flags ---------------------------------------

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


# --- occurrence_count routing ----------------------------------------------

def test_occurrence_count_single_dict(monkeypatch):
    captured = {}

    def fake_get_occurrence_index(name, normalize_kana, prefix_matching, suffix_matching, honorific_folding):
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

        def get(self, expression, reading):
            self.calls.append((expression, reading))
            return 99

    fake = FakeCombined()
    monkeypatch.setattr(dm, "get_combined_occurrence_index", lambda *a, **k: fake)
    count = occurrence_count(["A", "B"], "茶", "ちゃ")
    assert count == 99
    assert fake.calls == [("茶", "ちゃ")]


def test_occurrence_count_normalize_kana(monkeypatch):
    seen = {}

    def fake_get_occurrence_index(name, normalize_kana, prefix_matching, suffix_matching, honorific_folding):
        idx = OccurrenceIndex()
        idx.add("ぎりぎり", "ぎりぎり", 8)  # hiragana key
        return idx

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    # katakana input folds to hiragana before lookup
    count = occurrence_count(["D"], "ギリギリ", "ギリギリ", normalize_kana=True)
    assert count == 8


# --- _build_index_from_raw: meta shapes & filtering -------------------------

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


# --- kana-only (㋕) attribution --------------------------------------------

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


# --- honorific folding ------------------------------------------------------

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
    # bare form itself — お茶の間-only media must still credit a 茶の間 card.
    idx = _build_index_from_raw([["お茶の間", "freq", 9]], honorific_folding=True)
    assert idx.honorific_to_count.get("茶の間") == 9
    assert idx.get_total("茶の間", "ちゃのま", honorific_folding=True) == 9


def test_build_honorific_folding_credits_single_kanji_base():
    # The documented お金→金 case: single-kanji remainders are wanted folds too.
    idx = _build_index_from_raw([["お金", "freq", 40]], honorific_folding=True)
    assert idx.honorific_to_count.get("金") == 40


def test_build_honorific_folding_skips_kana_base_absent_from_dict():
    # Kana-only strips stay gated on dict membership — おかず is not お+かず, and
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


# --- prefix_total edges -----------------------------------------------------

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


# --- single-kanji phrase matching -------------------------------------------

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


def test_phrase_total_requires_non_empty_tail():
    idx = OccurrenceIndex()
    idx.add("最も", "もっとも", 12)
    # reading would validate (もっとも starts with もっと+も) but there is no tail
    assert idx.get_total("最", "もっと", prefix_matching=True) == 0


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
    def fake_get_occurrence_index(name, normalize_kana, prefix_matching, suffix_matching, honorific_folding):
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


# --- suffix_total / suffix_matching -----------------------------------------

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
    # otherwise absorb its whole compound family (語 -> 日本語/英語/…) — the volume/wrongness
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
    # so the gate excludes it — no suffix credit for する / こと / katakana loanwords.
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
    # nothing and there is no collision to guard — the honorific fold applies once.
    idx = _build_index_from_raw(
        [["茶", "freq", 10], ["お茶", "freq", 50]],
        honorific_folding=True,
    )
    assert idx.get_total("茶", "ちゃ", suffix_matching=True) == 10   # suffix ineligible -> exact only
    assert idx.get_total("茶", "ちゃ", suffix_matching=True, honorific_folding=True) == 60  # exact 10 + fold 50


def test_suffix_honorific_kana_fold_not_subsumed():
    # A kana-only stripped fold (しゃれ←おしゃれ) is NOT suffix-eligible (no kanji), so the
    # honorific credit must survive when both flags are on — the skip only fires when the
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
    # flags are on — accepted, and pinned here so the behavior can't silently change.
    idx = OccurrenceIndex()
    idx.add("一歩", "いっぽ", 5)
    idx.add("一歩一歩", "いっぽいっぽ", 40)
    assert idx.get_total("一歩", "いっぽ", prefix_matching=True) == 45
    assert idx.get_total("一歩", "いっぽ", suffix_matching=True) == 45
    assert idx.get_total("一歩", "いっぽ", prefix_matching=True, suffix_matching=True) == 85


def test_suffix_normalize_kana_end_to_end(monkeypatch):
    # A multi-char kanji-bearing card still triggers after katakana->hiragana folding.
    def fake_get_occurrence_index(name, normalize_kana, prefix_matching, suffix_matching, honorific_folding):
        return _build_index_from_raw(
            [["中学校", "freq", {"reading": "チュウガッコウ", "value": 100}],
             ["学校", "freq", {"reading": "ガッコウ", "value": 5}]],
            normalize_kana=normalize_kana,
        )

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    count = occurrence_count(["D"], "学校", "ガッコウ", normalize_kana=True, suffix_matching=True)
    assert count == 105


# --- single-kanji suffix phrase carve-out (mirror of the prefix phrase tests) ----

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
    # homograph but not the mismatched one (敵/かたき ← 目の敵/めのかたき, 敵/てき not) — the tail
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
    def fake_get_occurrence_index(name, normalize_kana, prefix_matching, suffix_matching, honorific_folding):
        return _build_index_from_raw(
            [["母の日", "freq", {"reading": "ハハノヒ", "value": 12}]],
            normalize_kana=normalize_kana,
        )

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)
    count = occurrence_count(["D"], "日", "ヒ", normalize_kana=True, suffix_matching=True)
    assert count == 12


# --- CombinedOccurrenceIndex memo eviction ----------------------------------

def test_combined_index_evicts_oldest_when_cap_reached(monkeypatch):
    monkeypatch.setattr(dm, "_COMBINED_MEMO_CAP", 2)

    def fake_get_occurrence_index(name, normalize_kana, prefix_matching, suffix_matching, honorific_folding):
        return OccurrenceIndex()  # every lookup totals to 0; we only test eviction

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)

    ci = CombinedOccurrenceIndex(["D1", "D2"])
    ci.get("x", "rx")
    ci.get("y", "ry")
    ci.get("z", "rz")  # cap reached -> oldest ("x","rx") evicted

    assert ("z", "rz") in ci.expr_reading_to_count
    assert ("x", "rx") not in ci.expr_reading_to_count
    assert len(ci.expr_reading_to_count) == 2
