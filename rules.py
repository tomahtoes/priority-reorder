from typing import Optional, Tuple
import re

# `limit=N` is a reorder-only control (take the top N cards of a priority bucket),
# not an Anki search term, so it is stripped here. The custom search terms
# (occurrences:/f/kanji:) are left in the query and resolved downstream by the
# patched Collection.find_cards (see search.py). The lookbehind keeps it from
# firing inside a larger token like `mylimit=3`.
LIMIT_PATTERN = re.compile(r"(?<!\w)limit=(?P<limit>\d+)")


def parse_rule_string(rule_string: str) -> Tuple[str, Optional[int]]:
    """Split a priority/normal search string into (anki_query, limit).

    `limit=N` is extracted and removed; everything else — including the custom
    occurrences:/f/kanji: terms — is returned verbatim as the query to hand to
    find_cards."""
    limit = None
    m_limit = LIMIT_PATTERN.search(rule_string)
    if m_limit:
        limit = int(m_limit.group("limit"))
        rule_string = LIMIT_PATTERN.sub("", rule_string)

    return " ".join(rule_string.split()), limit
