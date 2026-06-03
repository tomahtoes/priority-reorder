import dictionary_manager as dm
from dictionary_manager import OccurrenceIndex, expand_dict_names, occurrence_count


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
