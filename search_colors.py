"""Color-coding for search-query terms shown in the summary window.

A query token is keyed off its leading keyword (``kanji:new>=1`` -> ``kanji``,
``f>=1`` -> ``f``, ``s:7`` -> ``seen``, bare word -> the word). ``NO_COLOR_KEYS`` stay default,
``KNOWN_KEYWORD_COLORS`` get a fixed hue, the rest hash into ``OVERFLOW_PALETTE``.
Pure module (no ``aqt`` import) so it can be tested standalone.
"""

import hashlib
import html
import re
from typing import List, Optional

# Keywords that are never colored, i.e. kept at the default text color.
NO_COLOR_KEYS = frozenset(
    {
        "deck",
        "prop",
        "rated",
        "introduced",
        "note",
        "card",
        "flag",
        "nid",
        "cid",
        "mid",
        "re",
        "nc",
    }
)

# Boolean / structural tokens, never colored.
_STRUCTURAL = frozenset({"and", "or", "(", ")", "*", "-"})

# Fixed Monokai hues for important keywords; all kept perceptually distinct.
KNOWN_KEYWORD_COLORS = {
    "dark": {
        "kanji": "#A6E22E",        # green
        "occurrences": "#5C9DFF",  # blue
        "f": "#E6DB74",            # yellow
        "limit": "#F92672",        # pink
        "tag": "#AE81FF",          # purple
        "is": "#66D9EF",           # cyan
        "added": "#FD971F",        # orange
        "edited": "#FF5FD0",       # magenta
    },
    "light": {
        "kanji": "#4E8F00",        # green
        "occurrences": "#2348C4",  # blue
        "f": "#8A7B00",            # yellow
        "limit": "#D6206A",        # pink
        "tag": "#7C3AED",          # purple
        "is": "#0E7490",           # cyan
        "added": "#C45508",        # orange
        "edited": "#800077",       # magenta
    },
}

# Fallback hues for unknown keywords / bare words, chosen to stay perceptually
# distinct from each other and from every known hue (verified via CIE76 ΔE).
_RAW_OVERFLOW = {
    "dark": [
        "#26FFB7",  # spring green
        "#E67C67",  # coral
        "#ED5BFF",  # orchid
        "#5BFF5B",  # green
        "#FF6A47",  # vermilion
    ],
    "light": [
        "#6B362B",  # brown
        "#056B41",  # green
        "#384D8C",  # indigo
        "#8C0712",  # dark red
        "#8C3862",  # plum
    ],
}


def _build_overflow(variant: str) -> List[str]:
    # Drop any overflow hue that coincides with a known one, so the guarantee
    # holds even if the tables above are edited later.
    known = set(KNOWN_KEYWORD_COLORS[variant].values())
    return [c for c in _RAW_OVERFLOW[variant] if c not in known]


OVERFLOW_PALETTE = {
    "dark": _build_overflow("dark"),
    "light": _build_overflow("light"),
}

# A token is a maximal run of (quoted block | non-space char), so quoted
# segments like tag:"my tag" stay one token.
_TOKEN_RE = re.compile(r'(?:"[^"]*"|[^\s"])+')
_COLON_RE = re.compile(r"^([\w-]+):")
_OP_RE = re.compile(r"^([A-Za-z]+)(?:>=|<=|!=|=|<|>)\d+$")
# Grouping parens ride along on a term's token; they are split off so the
# bracket itself stays uncolored instead of taking the term's hue.
_PARENS_RE = re.compile(r"^(-*\(+)?(.*?)(\)+)?$", re.DOTALL)

# The addon's search terms each answer to their first letter too (see search.py). Both
# spellings have to fold to one key, or the same term takes two hues depending on how it
# was typed. Split by grammar, because the letters are not interchangeable across it:
# `seen:`/`occurrences:`/`kanji:` take a colon, `length` an operator. Neither map touches
# the bare-word fallthrough, where a lone `s` is a text search rather than `seen:`.
_COLON_ALIASES = {"o": "occurrences", "k": "kanji", "s": "seen"}
_OP_ALIASES = {"l": "length"}


def _color_key(token: str) -> Optional[str]:
    """The lookup key for a token, or None if it isn't a colorable term."""
    t = token.lstrip("-")
    if not t or t.lower() in _STRUCTURAL:
        return None
    m = _COLON_RE.match(t)
    if m:
        key = m.group(1).lower()
        return _COLON_ALIASES.get(key, key)
    m = _OP_RE.match(t)
    if m:
        key = m.group(1).lower()
        return _OP_ALIASES.get(key, key)
    return t.lower()


def color_for_key(key: str, *, dark: bool) -> Optional[str]:
    """Stateless color for a key (no overflow-collision handling; see
    :func:`colorize_query_html` for that)."""
    if key in NO_COLOR_KEYS:
        return None
    variant = "dark" if dark else "light"
    known = KNOWN_KEYWORD_COLORS[variant].get(key)
    if known is not None:
        return known
    palette = OVERFLOW_PALETTE[variant]
    idx = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % len(palette)
    return palette[idx]


def _blend(color: str, target: str, t: float) -> str:
    """Blend ``color`` toward ``target`` by fraction ``t`` (both ``#rrggbb``)."""
    c, g = color.lstrip("#"), target.lstrip("#")
    out = []
    for i in (0, 2, 4):
        a, b = int(c[i:i + 2], 16), int(g[i:i + 2], 16)
        out.append(round(a + (b - a) * t))
    return "#{:02x}{:02x}{:02x}".format(*out)


# How far a muted query's term colors are pulled toward the neutral target.
_MUTE_FACTOR = 0.5


def colorize_query_html(query: str, *, dark: bool, mute_toward: Optional[str] = None) -> str:
    """HTML for the query with each term wrapped in a colored span.

    Overflow colors are assigned per-query left-to-right: distinct keys never
    share one (linear-probe on collision; uncolored if the palette is exhausted).

    When ``mute_toward`` (a ``#rrggbb`` neutral) is given, every term's hue is
    blended toward it and otherwise-uncolored terms take that neutral, so the
    whole query reads de-emphasized while keeping its color identity.
    """
    variant = "dark" if dark else "light"
    palette = OVERFLOW_PALETTE[variant]
    used: set = set()
    cache: dict = {}

    def resolve(key: str) -> Optional[str]:
        if key in cache:
            return cache[key]
        if key in NO_COLOR_KEYS:
            cache[key] = None
            return None
        known = KNOWN_KEYWORD_COLORS[variant].get(key)
        if known is not None:
            cache[key] = known
            return known
        start = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % len(palette)
        color: Optional[str] = None
        for i in range(len(palette)):
            candidate = palette[(start + i) % len(palette)]
            if candidate not in used:
                color = candidate
                used.add(candidate)
                break
        cache[key] = color
        return color

    tokens: List[str] = _TOKEN_RE.findall(query or "")
    parts: List[str] = []
    for token in tokens:
        lead, core, trail = _PARENS_RE.match(token).groups()
        escaped = html.escape(core)
        key = _color_key(core) if core else None
        color = resolve(key) if key is not None else None
        if mute_toward is not None and core:
            color = _blend(color, mute_toward, _MUTE_FACTOR) if color else mute_toward
        if color:
            escaped = f'<span style="color:{color}">{escaped}</span>'
        parts.append(html.escape(lead or "") + escaped + html.escape(trail or ""))
    return " ".join(parts)
