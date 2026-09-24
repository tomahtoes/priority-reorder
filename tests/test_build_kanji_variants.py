"""parse_variants in tools/build_kanji_readings.py: KANJIDIC2 <variant> links -> glyph groups."""

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tool():
    path = os.path.join(ROOT, "tools", "build_kanji_readings.py")
    spec = importlib.util.spec_from_file_location("_build_kanji_readings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _character(literal, codes, variants=(), grade=None, freq=None):
    cps = "".join('<cp_value cp_type="%s">%s</cp_value>' % c for c in codes)
    vs = "".join('<variant var_type="%s">%s</variant>' % v for v in variants)
    misc = ""
    if grade is not None:
        misc += "<grade>%d</grade>" % grade
    if freq is not None:
        misc += "<freq>%d</freq>" % freq
    return ("<character><literal>%s</literal><codepoint>%s</codepoint>"
            "<dic_number>%s</dic_number><misc>%s</misc></character>" % (literal, cps, vs, misc))


def _xml(*characters):
    return "<kanjidic2>%s</kanjidic2>" % "".join(characters)


def test_links_resolve_through_cp_value_and_chain_into_one_group():
    xml = _xml(
        _character("剣", [("jis208", "1-23-85")], [("jis208", "1-49-88")], grade=8, freq=1500),
        _character("劍", [("jis208", "1-49-88")], [("jis212", "1-17-5")]),
        _character("劔", [("jis212", "1-17-5")]),
    )
    assert _tool().parse_variants(xml) == [["剣", "劍", "劔"]]


def test_dictionary_index_variants_are_ignored():
    # nelson_c names a dictionary entry, not a character, even when the number collides with
    # some cp_value text.
    xml = _xml(
        _character("灯", [("jis208", "1-37-84")], [("nelson_c", "1-37-85")], grade=4),
        _character("燈", [("jis208", "1-37-85")]),
    )
    assert _tool().parse_variants(xml) == []


def test_the_jouyou_glyph_leads_the_group():
    xml = _xml(
        _character("燈", [("jis208", "1-37-85")], [("jis208", "1-37-84")]),
        _character("灯", [("jis208", "1-37-84")], grade=4, freq=1300),
    )
    assert _tool().parse_variants(xml) == [["灯", "燈"]]


def test_the_shipped_table_folds_the_measured_pairs():
    # Pairs found in the author's dictionaries, where only the glyph differs.
    import dictionary_manager as dm
    for a, b in [("燈", "灯"), ("醬", "醤"), ("搔", "掻"), ("籠", "篭"), ("剝", "剥"), ("濤", "涛")]:
        assert dm._kanji_skeleton(a) == dm._kanji_skeleton(b), (a, b)
