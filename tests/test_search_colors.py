import search_colors


# alias folding
#
# The colorizer keys a token off its leading keyword. Both spellings of a term have to
# reach the same key, or a query reads in two different hues depending on how it was typed.

def test_colon_aliases_fold_onto_the_long_form():
    assert search_colors._color_key("o:MyDict>=5") == "occurrences"
    assert search_colors._color_key("k:new>=1") == "kanji"
    assert search_colors._color_key("s:7") == "seen"
    assert search_colors._color_key("l>=3") == "length"


def test_alias_and_long_form_get_the_same_color():
    for dark in (True, False):
        for alias, long_form in (("o:D>5", "occurrences:D>5"), ("k:num=2", "kanji:num=2"),
                                 ("s:7", "seen:7"), ("l>=3", "length>=3")):
            key_a = search_colors._color_key(alias)
            key_b = search_colors._color_key(long_form)
            assert search_colors.color_for_key(key_a, dark=dark) == \
                search_colors.color_for_key(key_b, dark=dark)


def test_negation_and_parens_do_not_disturb_the_alias_key():
    assert search_colors._color_key("-s:7") == "seen"
    assert search_colors._color_key("-o:D>5") == "occurrences"


def test_a_bare_letter_stays_a_text_search():
    # Only the keyword branches alias. A lone `s` in a query is a word to look for,
    # not `seen:`, and `l:5` is a field search, not the length term.
    assert search_colors._color_key("s") == "s"
    assert search_colors._color_key("l") == "l"
    assert search_colors._color_key("l:5") == "l"
    assert search_colors._color_key("o") == "o"


def test_anki_keys_are_untouched_by_the_alias_maps():
    assert search_colors._color_key("sc:5") == "sc"
    assert search_colors._color_key("nc:foo") == "nc"
    assert search_colors._color_key("added:3") == "added"
    assert search_colors._color_key("limit=5") == "limit"
