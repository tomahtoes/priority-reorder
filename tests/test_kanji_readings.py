"""Unit tests for the per-kanji reading matcher backing kanji:new_reading.

The interesting behaviour is all in which reading each kanji gets credited with,
so most tests assert on the human-readable slot description rather than the
encoded key. Every category the matcher has to survive is covered deliberately:
regular on/kun, okurigana disambiguation, rendaku, gemination, 湯桶/重箱
compounds, jukujikun/ateji, conjugated and compound verbs, and malformed fields.
"""

import pytest

import kanji_readings as kr


def slots(expression, reading):
    return [kr.describe_slot(s) for s in kr.reading_slots(expression, reading)]


# --- regular readings -----------------------------------------------------

@pytest.mark.parametrize("expression,reading,expected", [
    ("見物", "けんぶつ", ["見=けん", "物=ぶつ"]),          # both on
    ("山", "やま", ["山=やま"]),                          # single kun
    ("食事", "しょくじ", ["食=しょく", "事=じ"]),
    ("食べる", "たべる", ["食=た"]),                       # okurigana not credited
    ("生物", "せいぶつ", ["生=せい", "物=ぶつ"]),
    ("生きる", "いきる", ["生=い"]),
    ("場所", "ばしょ", ["場=ば", "所=しょ"]),              # 湯桶
    ("手本", "てほん", ["手=て", "本=ほん"]),              # 湯桶
    ("荷物", "にもつ", ["荷=に", "物=もつ"]),              # 重箱
])
def test_regular_readings(expression, reading, expected):
    assert slots(expression, reading) == expected


def test_same_kanji_twice_with_different_readings():
    # Slots are per occurrence, not per distinct kanji, and 日 is credited
    # separately for にち and for ひ (surfacing as び).
    assert slots("日曜日", "にちようび") == ["日=にち", "曜=よう", "日=ひ"]


# --- okurigana disambiguation ---------------------------------------------

def test_okurigana_length_disambiguates_stems():
    # 明 has both あ and あか; only the reading's own length can separate them.
    assert slots("明ける", "あける") == ["明=あ"]
    assert slots("明るい", "あかるい") == ["明=あか"]


def test_inflections_of_one_stem_share_a_slot():
    # 上がる / 上げる are the same reading of 上 to a learner, and stem-keyed
    # slots say so; 上る is a genuinely different reading.
    assert kr.reading_slots("上がる", "あがる") == kr.reading_slots("上げる", "あげる")
    assert kr.reading_slots("上る", "のぼる") != kr.reading_slots("上がる", "あがる")


# --- rendaku --------------------------------------------------------------

@pytest.mark.parametrize("expression,reading,expected", [
    ("花火", "はなび", ["花=はな", "火=ひ"]),
    ("手紙", "てがみ", ["手=て", "紙=かみ"]),
    ("三本", "さんぼん", ["三=さん", "本=ほん"]),
    ("人々", "ひとびと", ["人=ひと", "人=ひと"]),   # 々 expands, second rendakus
    ("時々", "ときどき", ["時=とき", "時=とき"]),
])
def test_rendaku_is_credited_to_the_base_reading(expression, reading, expected):
    assert slots(expression, reading) == expected


def test_rendaku_does_not_mint_a_new_slot():
    """The whole point of resolving to base readings: 血 learned as ち must
    silence 鼻血/はなぢ. A surface-keyed slot would fire, wrongly."""
    bare = kr.reading_slots("血", "ち")
    compound = kr.reading_slots("鼻血", "はなぢ")
    assert bare[0] in compound


def test_rendaku_is_suppressed_word_initially():
    # 花 leads the word, so it keeps はな; a voiced ばな must not be considered.
    assert slots("花火", "はなび")[0] == "花=はな"


# --- gemination and 促音添加 ----------------------------------------------

@pytest.mark.parametrize("expression,reading,expected", [
    ("学校", "がっこう", ["学=がく", "校=こう"]),
    ("一本", "いっぽん", ["一=いち", "本=ほん"]),   # gemination + handakuten
    ("十本", "じっぽん", ["十=じゅう", "本=ほん"]),  # じゅう -> じっ
    ("発表", "はっぴょう", ["発=はつ", "表=ひょう"]),
    ("三日", "みっか", ["三=み", "日=か"]),          # 促音添加: み -> みっ
])
def test_sound_changes(expression, reading, expected):
    assert slots(expression, reading) == expected


# --- conjugated and compound verbs ----------------------------------------

@pytest.mark.parametrize("expression,reading,expected", [
    ("引き取る", "ひきとる", ["引=ひ", "取=と"]),   # 連用形 changes the okurigana
    ("見送る", "みおくる", ["見=み", "送=おく"]),   # first element loses okurigana
    ("押しつぶす", "おしつぶす", ["押=お"]),
    ("続き", "つづき", ["続=つづ"]),
    ("出来る", "できる", ["出=で", "来=き"]),
])
def test_conjugated_and_compound_verbs_resolve(expression, reading, expected):
    assert slots(expression, reading) == expected


# --- jukujikun / ateji ----------------------------------------------------

@pytest.mark.parametrize("expression,reading", [
    ("火傷", "やけど"), ("今日", "きょう"), ("田舎", "いなか"), ("大人", "おとな"),
    ("七夕", "たなばた"), ("紅葉", "もみじ"), ("浴衣", "ゆかた"), ("二十歳", "はたち"),
])
def test_jukujikun_leaves_every_kanji_unresolved(expression, reading):
    keys = kr.reading_slots(expression, reading)
    assert kr.unresolved_count(keys) == len(keys)
    # One span over the whole unexplainable stretch, not a split at an
    # arbitrary non-mora boundary.
    spans = {k.split(kr._UNRESOLVED, 1)[1] for k in keys}
    assert len(spans) == 1


def test_partial_ateji_only_flags_what_it_cannot_explain():
    # 眼=め is a real reading and must be credited; only 鏡/がね is irregular.
    assert slots("眼鏡", "めがね") == ["眼=め", "鏡=?がね"]
    assert kr.unresolved_count(kr.reading_slots("眼鏡", "めがね")) == 1


def test_same_spelling_different_reading_gets_different_slots():
    assert kr.reading_slots("紅葉", "こうよう") != kr.reading_slots("紅葉", "もみじ")
    assert kr.unresolved_count(kr.reading_slots("紅葉", "こうよう")) == 0


def test_unresolved_slots_are_stable_across_encounters():
    """An unresolved slot carries its span, so learning 火傷 credits it and the
    card stops firing -- while a different ateji word using 火 still fires. A
    slot meaning only "unexplained" would fire forever."""
    yakedo = kr.reading_slots("火傷", "やけど")
    assert kr.reading_slots("火傷", "やけど") == yakedo
    assert not set(yakedo) & set(kr.reading_slots("火事", "かじ"))


# --- counters and iteration marks -----------------------------------------

@pytest.mark.parametrize("expression,reading,expected", [
    ("一人", "ひとり", ["一=ひと", "人=り"]),
    ("二人", "ふたり", ["二=ふた", "人=り"]),
    ("一ヶ月", "いっかげつ", ["一=いち", "月=げつ"]),  # ヶ is the 箇 abbreviation
])
def test_counters(expression, reading, expected):
    assert slots(expression, reading) == expected


def test_katakana_in_expression_folds():
    assert slots("缶ビール", "かんビール") == ["缶=かん"]


# --- field sanitisation ---------------------------------------------------

@pytest.mark.parametrize("reading", [
    "やけど", "<b>やけど</b>", "やけど&nbsp;", "  やけど  ", "火[や]傷[けど]",
])
def test_markup_in_the_reading_field_is_stripped(reading):
    """A reading field holding markup would otherwise wildcard everything, and
    the term would silently match every card without raising."""
    assert kr.reading_slots("火傷", reading) == kr.reading_slots("火傷", "やけど")


def test_anki_furigana_is_usable_not_merely_tolerated():
    assert slots("食べる", "食[た]べる") == ["食=た"]


# --- degenerate input -----------------------------------------------------

@pytest.mark.parametrize("expression,reading", [
    ("ひらがな", "ひらがな"),   # no kanji at all
    ("火傷", ""),               # no reading -> no information, not "nothing new"
    ("", "やけど"),
])
def test_no_slots_when_there_is_nothing_to_say(expression, reading):
    assert kr.reading_slots(expression, reading) == ()


def test_expression_longer_than_the_guard_is_all_unresolved():
    n = kr._MAX_EXPR + 1
    got = kr.reading_slots("国" * n, "こく" * n)
    assert len(got) == n
    assert kr.unresolved_count(got) == n


def test_kanji_absent_from_the_table_is_unresolved_not_an_error():
    missing = "\U00020000"
    assert kr._load().get(missing) is None
    got = kr.reading_slots(missing, "あ")
    assert kr.unresolved_count(got) == len(got) == 1


def test_reading_that_contradicts_the_expression_still_reports_every_kanji():
    # Nothing reaches the end of both strings; every kanji must still be listed.
    got = kr.reading_slots("食べる", "しょくじ")
    assert len(got) == 1 and kr.unresolved_count(got) == 1


# --- encoding invariants --------------------------------------------------

def test_kana_codec_round_trips():
    text = "".join(kr._KANA)
    assert kr.decode_kana(kr.encode_kana(text)) == text


def test_reserved_characters_are_not_reachable_by_the_codec():
    """A kana encoding to '*' would be indistinguishable from an unresolved
    slot, and unresolved_count would miscount every word using that kana."""
    encoded = kr.encode_kana("".join(kr._KANA))
    for reserved in kr._RESERVED:
        assert reserved not in encoded


def test_kanji_pass_through_the_codec_untouched():
    assert kr.encode_kana("食べる")[0] == "食"


# --- caching --------------------------------------------------------------

def test_memo_is_clearable():
    kr.clear_cache()
    assert kr._MEMO == {}
    kr.reading_slots("火傷", "やけど")
    assert kr._MEMO
    kr.clear_cache()
    assert kr._MEMO == {}


# --- laziness and cost ----------------------------------------------------

def test_table_is_not_loaded_at_import():
    """A collection that never runs a new_reading term must not pay to read or
    parse the table. Asserted structurally rather than by timing."""
    import importlib
    module = importlib.reload(kr)
    assert module._TABLE is None
    module.reading_slots("山", "やま")
    assert module._TABLE is not None


def test_expansion_is_per_kanji_and_cached():
    kr._EXPANDED.clear()
    kr.clear_cache()
    kr.reading_slots("食事", "しょくじ")
    touched = set(kr._EXPANDED)
    # Only the kanji actually seen, not all ~12k in the table.
    assert touched == {"食", "事"}
    kr.reading_slots("食事", "しょくじ")
    assert set(kr._EXPANDED) == touched


def test_repeat_lookups_hit_the_memo_instead_of_rematching(monkeypatch):
    kr.clear_cache()
    kr.reading_slots("見物", "けんぶつ")
    calls = []
    monkeypatch.setattr(kr, "_exact", lambda *a, **k: calls.append(1))
    assert kr.reading_slots("見物", "けんぶつ")   # served from the memo
    assert calls == []


def test_memo_is_bounded():
    """Evicts oldest-first at the cap rather than growing without limit, and
    rather than clearing wholesale (which would thrash a large working set)."""
    kr.clear_cache()
    cap, original = 8, kr._MEMO_CAP
    kr._MEMO_CAP = cap
    try:
        for i in range(cap * 3):
            kr.reading_slots("山", "やま" + "あ" * i)
        assert len(kr._MEMO) <= cap
    finally:
        kr._MEMO_CAP = original
        kr.clear_cache()
