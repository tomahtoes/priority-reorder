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

    def fake_get_occurrence_index(name, normalize_kana, prefix_matching, honorific_folding):
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

    def fake_get_occurrence_index(name, normalize_kana, prefix_matching, honorific_folding):
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


def test_build_honorific_folding_skips_when_base_absent():
    idx = _build_index_from_raw([["お土産", "freq", 12]], honorific_folding=True)
    assert idx.honorific_to_count == {}


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


# --- CombinedOccurrenceIndex memo eviction ----------------------------------

def test_combined_index_evicts_oldest_when_cap_reached(monkeypatch):
    monkeypatch.setattr(dm, "_COMBINED_MEMO_CAP", 2)

    def fake_get_occurrence_index(name, normalize_kana, prefix_matching, honorific_folding):
        return OccurrenceIndex()  # every lookup totals to 0; we only test eviction

    monkeypatch.setattr(dm, "get_occurrence_index", fake_get_occurrence_index)

    ci = CombinedOccurrenceIndex(["D1", "D2"])
    ci.get("x", "rx")
    ci.get("y", "ry")
    ci.get("z", "rz")  # cap reached -> oldest ("x","rx") evicted

    assert ("z", "rz") in ci.expr_reading_to_count
    assert ("x", "rx") not in ci.expr_reading_to_count
    assert len(ci.expr_reading_to_count) == 2
