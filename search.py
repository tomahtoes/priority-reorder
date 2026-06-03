"""Custom search terms (`occurrences:`, `f`, `kanji:`) as real Anki searches.

Anki's (Rust) search backend can't accept custom terms, so we rewrite each custom
token into a concrete `nid:` clause before the query reaches the backend, at two
idempotent chokepoints (the browser hook and the Collection methods). This makes
the same terms the reorderer understands work in the Browse bar and via the
collection API / AnkiConnect.

Top-level imports stay light (no `aqt`) so this module loads under pytest; anything
touching the collection is imported lazily inside the resolvers / install().
"""

import logging
import re

try:  # inside Anki: isolated package namespace
    from .utils import parse_comparator
except ImportError:  # pytest / flat-import context
    from utils import parse_comparator

logger = logging.getLogger("priority_reorder.search")

# Each pattern carries a standalone-token left lookbehind `(?<![^\s(-])` so it only
# fires at the start of a token (after start / space / "(" / "-") and never inside a
# larger token. A leading "-" is NOT consumed, so Anki's own negation wraps the
# replacement group for free.
OCC_RE = re.compile(
    r"(?<![^\s(-])occurrences:(?P<dict>[^=<>!\s]+)(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)"
)
# The mandatory operator right after `f` keeps this from firing inside `flag:` /
# `front:` etc.; the trailing boundary keeps the threshold from running into the
# next token.
FREQ_RE = re.compile(
    r"(?<![^\s(-])f(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)(?=\s|\)|$)"
)
KANJI_RE = re.compile(
    r"(?<![^\s(-])kanji:(?P<type>new|num)(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)"
)


def has_custom_term(query: str) -> bool:
    """True if `query` contains any of the addon's custom search terms. Used for
    cosmetic stats labeling; the actual resolution happens in rewrite_query."""
    if not query:
        return False
    return bool(OCC_RE.search(query) or KANJI_RE.search(query) or FREQ_RE.search(query))


def _format_nid_clause(ids) -> str:
    if not ids:
        return "nid:0"  # id 0 never exists -> matches nothing
    return "(nid:" + ",".join(str(i) for i in ids) + ")"


def parse_custom_terms(query):
    """Pull the custom tokens out of `query` for the reorder post-filter fast path
    (see DataManager.get_cards_from_search). Returns a list of (kind, args, negated):

        ("occ",   (dict_str, op, thresh), negated)
        ("kanji", (check_type, op, thresh), negated)
        ("freq",  (op, thresh), negated)

    `negated` is True when the token was immediately preceded by '-' (Anki's
    conjunctive NOT — the regexes leave that '-' unconsumed). Order doesn't matter
    for the pure conjunctions this path handles."""
    terms = []

    def negated(m):
        return m.start() > 0 and query[m.start() - 1] == "-"

    for m in OCC_RE.finditer(query):
        terms.append(("occ", (m.group("dict"), m.group("op"), int(m.group("thresh"))), negated(m)))
    for m in KANJI_RE.finditer(query):
        terms.append(("kanji", (m.group("type"), m.group("op"), int(m.group("thresh"))), negated(m)))
    for m in FREQ_RE.finditer(query):
        terms.append(("freq", (m.group("op"), int(m.group("thresh"))), negated(m)))
    return terms


def _strip_custom_terms(query: str) -> str:
    """Blank out every custom token, leaving the standard Anki part of the query.
    A leading `-` on a negated term is not consumed by the regexes, so it survives
    here and is dropped by the caller before being handed to find_notes."""
    q = OCC_RE.sub(" ", query)
    q = KANJI_RE.sub(" ", q)
    q = FREQ_RE.sub(" ", q)
    return q


def _candidate_restriction_allowed(query: str, stripped: str) -> bool:
    """True when resolving the custom terms over only the notes matched by the
    standard part of the query is guaranteed to give the same result as a full
    scan. That holds exactly when the query is a pure top-level conjunction with a
    non-empty standard part (`A AND custom ⊆ A`). Conservatively bail otherwise:

      - empty standard part (bare custom term) — nothing to restrict on;
      - a standalone OR/or token — disjunction would drop valid matches;
      - parentheses — grouping may hide a disjunction we can't cheaply prove safe.

    Bailing only costs speed (full scan), never correctness."""
    if not stripped.replace("-", "").strip():
        return False
    if "(" in query or ")" in query:
        return False
    return not any(tok.lower() == "or" for tok in query.split())


def _call_resolver(resolver, injected, candidate_nids, *args):
    """Invoke a resolver. Injected (test) resolvers keep the original positional
    signature; the real resolvers also accept the candidate-set restriction."""
    if injected:
        return resolver(*args)
    return resolver(*args, candidate_nids=candidate_nids)


def rewrite_query(query, *, occ_resolver=None, freq_resolver=None, kanji_resolver=None, find_notes=None) -> str:
    """Replace every custom token in `query` with a concrete `nid:` clause.

    Resolvers are injectable for testing and default to the real ones:
      occ_resolver(dict_str, op, thresh) -> list[int]
      freq_resolver(op, thresh) -> list[int]
      kanji_resolver(check_type, op, thresh) -> list[int]
    Idempotent: the output contains no custom token.
    """
    if not query:
        return query
    has_occ = "occurrences:" in query
    has_kanji = "kanji:" in query
    has_freq = bool(FREQ_RE.search(query))
    if not (has_occ or has_kanji or has_freq):
        return query  # fast path: nothing to resolve

    injected = (occ_resolver is not None or freq_resolver is not None or kanji_resolver is not None)
    occ = occ_resolver or resolve_occurrences
    freq = freq_resolver or resolve_frequency
    kanji = kanji_resolver or resolve_kanji

    # Restrict resolution to the notes the standard part of the query already
    # selects, so e.g. `deck:X occurrences:D>5` evaluates the occurrence predicate
    # over deck X only instead of the whole collection. Skipped when resolvers are
    # injected (tests pass no collection) or when restriction would be unsafe
    # (see _candidate_restriction_allowed) — those fall back to the full scan.
    candidate_nids = None
    if not injected:
        fn = find_notes if find_notes is not None else _default_find_notes()
        if fn is not None:
            stripped = " ".join(_strip_custom_terms(query).split())
            if _candidate_restriction_allowed(query, stripped):
                base = " ".join(t for t in stripped.split() if t != "-")
                try:
                    candidate_nids = set(fn(base))
                except Exception:
                    logger.exception("candidate find_notes failed; falling back to full scan")
                    candidate_nids = None

    if has_occ:
        query = OCC_RE.sub(
            lambda m: _format_nid_clause(_call_resolver(
                occ, injected, candidate_nids, m.group("dict"), m.group("op"), int(m.group("thresh")))),
            query,
        )
    if has_kanji:
        query = KANJI_RE.sub(
            lambda m: _format_nid_clause(_call_resolver(
                kanji, injected, candidate_nids, m.group("type"), m.group("op"), int(m.group("thresh")))),
            query,
        )
    if has_freq:
        query = FREQ_RE.sub(
            lambda m: _format_nid_clause(_call_resolver(
                freq, injected, candidate_nids, m.group("op"), int(m.group("thresh")))),
            query,
        )
    return query


def _safe_rewrite(query: str) -> str:
    try:
        return rewrite_query(query, find_notes=_default_find_notes())
    except Exception:
        logger.exception("custom-term rewrite failed; passing query through unchanged")
        return query


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

# Cache of resolved nid lists, keyed by (token_string, collection signature). A
# single reorder run issues many find_cards calls and repeated browser searches
# re-resolve the same tokens; this avoids re-scanning the collection each time.
# Correctness-first: any collection change (mw.col.mod) invalidates the whole memo.
_resolution_cache = {}
_resolution_sig = None


def _resolve(key, compute, candidate_nids):
    """Run a resolver's compute() with the right caching scope.

    Full-scan results (candidate_nids is None) are candidate-independent, so they
    are memoized (and the whole memo is dropped whenever the collection changes).
    Restricted results depend on the candidate set, are already cheap, and must
    never be served to a different query — so they are computed fresh, unmemoized."""
    if candidate_nids is not None:
        return compute()

    global _resolution_sig
    from aqt import mw

    sig = mw.col.mod
    if sig != _resolution_sig:
        _resolution_cache.clear()
        _resolution_sig = sig
    if key in _resolution_cache:
        return _resolution_cache[key]
    result = compute()
    _resolution_cache[key] = result
    return result


def _iter_candidate_notes(required_fields, candidate_nids=None):
    """Yield (nid, {field_name: value}) for notes whose note type contains *all*
    of `required_fields`. Field ords are resolved once per note type.

    With `candidate_nids` given, only those notes are fetched in a single SQL pass
    (notes whose type lacks a required field are skipped, mirroring the full-scan
    note-type filter). Without it, every note of every matching type is scanned."""
    from aqt import mw

    # mid -> {field_name: ord}, for the note types that carry every required field.
    mid_fields = {}
    for model in mw.col.models.all():
        fmap = mw.col.models.field_map(model)  # name -> (ord, field_dict)
        if all(name in fmap for name in required_fields):
            mid_fields[model["id"]] = {name: fmap[name][0] for name in required_fields}

    def values_for(ords, flds):
        parts = flds.split("\x1f")
        return {name: (parts[o] if o < len(parts) else "") for name, o in ords.items()}

    if candidate_nids is not None:
        if not candidate_nids:
            return
        from anki.utils import ids2str

        for nid, mid, flds in mw.col.db.execute(
            f"select id, mid, flds from notes where id in {ids2str(candidate_nids)}"
        ):
            ords = mid_fields.get(mid)
            if ords is None:  # note type lacks a required field -> never a match
                continue
            yield nid, values_for(ords, flds)
        return

    for mid, ords in mid_fields.items():
        for nid, flds in mw.col.db.execute(
            "select id, flds from notes where mid = ?", mid
        ):
            yield nid, values_for(ords, flds)


def resolve_occurrences(dict_str, op, thresh, candidate_nids=None):
    """Note ids whose (expression, reading) occurrence total satisfies the
    threshold. Notes missing either field are skipped (never matched), mirroring
    the former OccurrenceRule.matches early-out."""
    from .config_manager import get_config
    from .dictionary_manager import expand_dict_names, occurrence_count

    def compute():
        cfg = get_config()
        expr_field = cfg.search_config.expression_field
        read_field = cfg.search_config.expression_reading_field
        dict_names = expand_dict_names(dict_str)
        comparator = parse_comparator(op)

        ids = []
        for nid, values in _iter_candidate_notes((expr_field, read_field), candidate_nids):
            expression = values[expr_field]
            reading = values[read_field]
            if not expression or not reading:
                continue
            count = occurrence_count(
                dict_names,
                expression,
                reading,
                normalize_kana=cfg.kana_normalization,
                combine_word_forms=cfg.combine_word_forms,
                prefix_matching=cfg.prefix_matching,
                honorific_folding=cfg.honorific_folding,
            )
            if comparator(count, thresh):
                ids.append(nid)
        return ids

    return _resolve(("occ", dict_str, op, thresh), compute, candidate_nids)


def resolve_frequency(op, thresh, candidate_nids=None):
    """Note ids whose sort-field value satisfies the threshold. Missing /
    non-numeric / <= 0 values resolve to +inf (same as the reorderer), so e.g.
    `f>BIG` still includes value-less notes."""
    from .config_manager import get_config
    from .utils import parse_sort_value

    def compute():
        cfg = get_config()
        sort_field = cfg.sort_field
        comparator = parse_comparator(op)

        ids = []
        for nid, values in _iter_candidate_notes((sort_field,), candidate_nids):
            value, _ = parse_sort_value(values[sort_field])
            if comparator(value, thresh):
                ids.append(nid)
        return ids

    return _resolve(("freq", op, thresh), compute, candidate_nids)


def resolve_kanji(check_type, op, thresh, candidate_nids=None):
    """Note ids whose expression has the requested kanji count. Notes with an
    empty expression are skipped (never matched), mirroring KanjiRule.matches."""
    from .config_manager import get_config
    from .kanji_manager import get_kanji_manager

    def compute():
        cfg = get_config()
        expr_field = cfg.search_config.expression_field
        comparator = parse_comparator(op)
        km = get_kanji_manager(cfg)

        ids = []
        for nid, values in _iter_candidate_notes((expr_field,), candidate_nids):
            expression = values[expr_field]
            if not expression:
                continue
            if check_type == "new":
                count = km.get_unknown_kanji_count(expression)
            else:  # "num"
                count = km.get_kanji_count(expression)
            if comparator(count, thresh):
                ids.append(nid)
        return ids

    return _resolve(("kanji", check_type, op, thresh), compute, candidate_nids)


# ---------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------

_installed = False
_original_find_cards = None
_original_find_notes = None


def _default_find_notes():
    """An unpatched ``find_notes(query) -> [nid]`` bound to the live collection,
    used to compute the candidate set for restricted resolution. Returns ``None``
    when no collection is available (headless pytest, or before a profile opens).

    The stripped query passed to it never contains custom tokens, so going through
    the still-patched ``mw.col.find_notes`` (pre-install) would not recurse — but we
    prefer the saved original once it exists to avoid the rewrite hop entirely."""
    try:
        from aqt import mw
    except Exception:
        return None
    if mw is None or getattr(mw, "col", None) is None:
        return None
    col = mw.col
    if _original_find_notes is not None:
        return lambda q: _original_find_notes(col, q)
    return lambda q: col.find_notes(q)


def _on_browser_will_search(ctx) -> None:
    ctx.search = _safe_rewrite(ctx.search)


def install() -> None:
    """Wire the custom terms into the browser and the collection API (idempotent)."""
    global _installed, _original_find_cards, _original_find_notes
    if _installed:
        return

    from aqt import gui_hooks
    from anki.collection import Collection

    gui_hooks.browser_will_search.append(_on_browser_will_search)

    _original_find_cards = Collection.find_cards
    _original_find_notes = Collection.find_notes

    def patched_find_cards(self, query, *args, **kwargs):
        return _original_find_cards(self, _safe_rewrite(query), *args, **kwargs)

    def patched_find_notes(self, query, *args, **kwargs):
        return _original_find_notes(self, _safe_rewrite(query), *args, **kwargs)

    Collection.find_cards = patched_find_cards
    Collection.find_notes = patched_find_notes
    _installed = True
