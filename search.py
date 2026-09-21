"""Custom search terms (`occurrences:`, `f`, `kanji:`, `seen:`, `length`) as real Anki searches.

Anki's Rust search backend cannot accept custom terms, so each custom token is rewritten into a
concrete `nid:` clause before the query reaches the backend, at two idempotent chokepoints: the
browser hook and the Collection methods. That makes the terms the reorderer understands work in
the Browse bar and through the collection API and AnkiConnect.

Each term also answers to its first letter (`o:`, `k:`, `s:`, `l`). Those chokepoints are
process-wide, so the letters are claimed for every addon in the Anki process. They are safe
because Anki reserves none of them (it does reserve the short keys `re:`, `nc:`, `sc:`, `w:`),
and each alias keeps the long form's mandatory operator or digit argument, which keeps it off
an ordinary field search like `o:foo`.

Top-level imports stay free of `aqt` so this module loads under pytest. Anything touching the
collection is imported lazily inside the resolvers and install().
"""

import logging
import re
from typing import Optional

try:  # inside Anki: isolated package namespace
    from .utils import parse_comparator
except ImportError:  # pytest / flat-import context
    from utils import parse_comparator

logger = logging.getLogger("priority_reorder.search")

# Each pattern carries a standalone-token left lookbehind `(?<![^\s(-])` so it only
# fires at the start of a token (after start / space / "(" / "-") and never inside a
# larger token. A leading "-" is NOT consumed, so Anki's own negation wraps the
# replacement group for free. That lookbehind is also what keeps the one-letter
# aliases off Anki's own keys and off field searches: `sc:5`, `cds:5`, `deck:s:5`,
# `on:foo>=1` and `nk:new>=1` all fail it.
#
# The mandatory operator keeps the `o:` alias off a plain field search like `o:foo`.
OCC_RE = re.compile(
    r"(?<![^\s(-])(?:occurrences|o):(?P<dict>[^=<>!\s]+)(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)"
)
# The mandatory operator right after `f` keeps this from firing inside `flag:` or `front:`,
# and the trailing boundary keeps the threshold from running into the next token. `f` is
# already the one-letter form, so it has no long spelling to alternate with.
FREQ_RE = re.compile(
    r"(?<![^\s(-])f(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)(?=\s|\)|$)"
)
# `length<op><n>` filters on the expression field's character count (Unicode code
# points of the raw value). Same shape as FREQ_RE: the mandatory operator keeps it
# off a real `length:` field search, the lookbehind off words like `wavelength`. The `l`
# alias wants the operator directly after the letter, so `limit=5` and `l:5` do not match.
LENGTH_RE = re.compile(
    r"(?<![^\s(-])(?:length|l)(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)(?=\s|\)|$)"
)
# `kanji:new` and `kanji:new_reading` take an optional bracketed target `[T]`:
# a kanji (resp. a reading of a kanji) counts as "new" until T learned words
# contain it. The bracket guard must stay a *negative* lookbehind: a positive `(?<=new)`
# is fixed-width and so could never admit a second, longer type name. `(?<!num)` means
# "allowed after anything but num" and stays fixed-width.
#
# Type names are listed longest-first for readability only. Ordering does not change any
# match: a failed continuation backtracks into the group at the same start position and
# tries the next name.
KANJI_RE = re.compile(
    r"(?<![^\s(-])(?:kanji|k):(?P<type>new_reading|new|num)"
    r"(?:(?<!num)\[(?P<target>\d+)\])?"
    r"(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)"
)
# `seen:N` is a date-windowed *presence* lookup over user_files/_seen/<date>/ (see seen_manager).
# N is the number of trailing daily dicts, and a word matches if it appears in ANY of them. It is
# boolean, with no count test. N<=0 matches nothing. The spec is a number rather than a name, so
# it never composes with occurrences: there is no `occurrences:seen` path to the seen data.
# The mandatory digits keep the `s:` alias off a field search like `s:foo`.
SEEN_RE = re.compile(
    r"(?<![^\s(-])(?:seen|s):(?P<n>\d+)"
)


def has_custom_term(query: str) -> bool:
    """True if `query` contains any of the addon's custom search terms. Used for
    cosmetic summary labeling; the actual resolution happens in rewrite_query."""
    if not query:
        return False
    return bool(
        OCC_RE.search(query)
        or KANJI_RE.search(query)
        or FREQ_RE.search(query)
        or SEEN_RE.search(query)
        or LENGTH_RE.search(query)
    )


def _format_nid_clause(ids) -> str:
    if not ids:
        return "nid:0"  # id 0 never exists -> matches nothing
    return "(nid:" + ",".join(str(i) for i in ids) + ")"


def _kanji_args(m):
    """(check_type, target, op, thresh) for a KANJI_RE match. `target` is the bracketed [T] on
    kanji:new and kanji:new_reading, defaulting to 1: a kanji, or a reading of one, stays "new"
    until T learned words contain it. The regex forbids a bracket on "num", where target is
    normalized to 1 as well so tuple shapes and cache keys stay uniform."""
    t = m.group("target")
    return m.group("type"), int(t) if t is not None else 1, m.group("op"), int(m.group("thresh"))


def parse_custom_terms(query):
    """Pull the custom tokens out of `query` for the reorder post-filter fast path
    (see DataManager.get_cards_from_search), as a list of (kind, args, negated):

        ("occ",    (dict_str, op, thresh), negated)
        ("kanji",  (check_type, target, op, thresh), negated)
        ("freq",   (op, thresh), negated)
        ("seen",   (n,), negated)
        ("length", (op, thresh), negated)

    `negated` is True when the token was immediately preceded by '-', Anki's conjunctive NOT.
    The regexes leave that '-' unconsumed. Order does not matter for the pure conjunctions this
    path handles."""
    terms = []

    def negated(m):
        return m.start() > 0 and query[m.start() - 1] == "-"

    for m in OCC_RE.finditer(query):
        terms.append(("occ", (m.group("dict"), m.group("op"), int(m.group("thresh"))), negated(m)))
    for m in KANJI_RE.finditer(query):
        terms.append(("kanji", _kanji_args(m), negated(m)))
    for m in FREQ_RE.finditer(query):
        terms.append(("freq", (m.group("op"), int(m.group("thresh"))), negated(m)))
    for m in SEEN_RE.finditer(query):
        terms.append(("seen", (int(m.group("n")),), negated(m)))
    for m in LENGTH_RE.finditer(query):
        terms.append(("length", (m.group("op"), int(m.group("thresh"))), negated(m)))
    return terms


def _strip_custom_terms(query: str) -> str:
    """Blank out every custom token, leaving the standard Anki part of the query.
    A leading `-` on a negated term is not consumed by the regexes, so it survives
    here and is dropped by the caller before being handed to find_notes."""
    q = OCC_RE.sub(" ", query)
    q = KANJI_RE.sub(" ", q)
    q = FREQ_RE.sub(" ", q)
    q = SEEN_RE.sub(" ", q)
    q = LENGTH_RE.sub(" ", q)
    return q


# A standalone `or` operator token: bounded by start/end/whitespace/parens, so
# `for`, `orange` and field words never match. Case-insensitive like Anki's.
_OR_TOKEN_RE = re.compile(r"(?i)(?<![^\s()])or(?![^\s()])")


def _depth_states(query: str):
    """``[(paren depth, inside quotes)]`` before each character, or None when the parens do not
    balance or a quote is never closed. Backslash escapes the next character.

    Shared by the two candidate-restriction predicates below, which need the same positional
    view of the query and must agree about what counts as malformed."""
    states = []
    depth = 0
    in_quotes = False
    escaped = False
    for ch in query:
        states.append((depth, in_quotes))
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif not in_quotes:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    return None
    if depth != 0 or in_quotes:
        return None
    return states


def _custom_term_spans(query: str):
    """``[(start, end)]`` of every custom token in the query, in no particular order."""
    spans = []
    for regex in (OCC_RE, KANJI_RE, FREQ_RE, SEEN_RE, LENGTH_RE):
        spans.extend(m.span() for m in regex.finditer(query))
    return spans


def _or_at_depth(query: str, states, lo: int, hi: int, depth: int) -> bool:
    """Whether an OR operator sits at exactly ``depth`` within ``query[lo:hi]``."""
    for m in _OR_TOKEN_RE.finditer(query, lo, hi):
        if states[m.start()] == (depth, False):
            return True
    return False


def _spans_at_depth(query: str, states, lo: int, hi: int, depth: int):
    """``query[lo:hi]`` split on whitespace sitting at exactly ``depth``, outside quotes.

    A parenthesized group is therefore one span however much whitespace it contains, and so is
    a quoted value like ``deck:"my deck"``."""
    spans = []
    start = None
    for i in range(lo, hi):
        if query[i].isspace() and states[i] == (depth, False):
            if start is not None:
                spans.append((start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        spans.append((start, hi))
    return spans


def candidate_base_query(query: str) -> Optional[str]:
    """The standard part of ``query`` whose matches are guaranteed to CONTAIN the whole query's,
    or None when no such part can be derived.

    Resolving a custom term over only those notes is then equivalent to a full scan:

        Let the query be a top-level conjunction C1 AND ... AND Ck with no OR at depth 0, and
        let K be the conjuncts holding no custom term. Then matches(query) is a subset of
        matches(AND K), the base returned here. Replacing a custom term's resolution S with
        S intersected with the base is match-preserving: a note inside the base sees the same
        truth value for that term, and a note outside it already fails some conjunct in K,
        which the substitution does not touch. That holds however deeply the term is nested,
        and whether or not it is negated.

    So whole conjuncts are DROPPED rather than the custom tokens blanked out in place. Blanking
    turns ``(seen:7 OR added:7)`` into ``( OR added:7)``, which is malformed, and is narrower
    rather than wider even read charitably, since it deletes a branch of the OR.

    A non-negated parenthesized group carrying no OR of its own is flattened into the enclosing
    conjunction. That is what lets the reorderer's own ``(user query) is:new`` wrapping still
    contribute the user's deck and other conjuncts, instead of collapsing into one opaque term
    that has to be dropped whole. A NEGATED group is never flattened: not (a and b) is not the
    same as (not a) and (not b).

    Returns None, meaning "fall back to a full scan", on a top-level OR (where the superset
    argument fails), on unbalanced parens or an unterminated quote, and when every conjunct
    carries a custom term so nothing is left to restrict on. Bailing costs speed, never
    correctness."""
    states = _depth_states(query)
    if states is None:
        return None
    if _or_at_depth(query, states, 0, len(query), 0):
        return None

    custom = _custom_term_spans(query)
    kept = []
    work = [(0, len(query), 0)]
    while work:
        lo, hi, depth = work.pop()
        for start, end in _spans_at_depth(query, states, lo, hi, depth):
            if (query[start] == "(" and query[end - 1] == ")"
                    and all(states[i][0] > depth for i in range(start + 1, end - 1))
                    and not _or_at_depth(query, states, start + 1, end - 1, depth + 1)):
                work.append((start + 1, end - 1, depth + 1))
            elif not any(start <= cs < end for cs, _ce in custom):
                kept.append((start, end))
    if not kept:
        return None
    kept.sort()
    return " ".join(query[start:end] for start, end in kept)


def _candidate_restriction_allowed(query: str, stripped: str) -> bool:
    """True when resolving the custom terms over only the notes the standard part of the query
    matches is guaranteed to equal a full scan. That holds for a TOP-LEVEL conjunction

        S1 ∧ ... ∧ Sk ∧ (¬)custom ...

    where each Si may internally contain parens or ORs. C = matches(S1 ∧ ... ∧ Sk) is a superset
    of the full query's matches, and a note outside C fails the standard conjuncts however its
    custom term resolves. Negated terms included: -nid:S only differs on notes in S\\C, which the
    standard part excludes either way. Allow iff:

      - the stripped standard part is non-empty (else nothing to restrict on);
      - every custom token sits at paren depth 0, outside double quotes;
      - no OR operator at depth 0 outside quotes (deeper ORs sit inside one conjunct);
      - parens balance and quotes terminate; any anomaly bails.

    Bailing costs speed, never correctness."""
    if not stripped.replace("-", "").strip():
        return False

    states = _depth_states(query)
    if states is None:
        return False

    for regex in (OCC_RE, KANJI_RE, FREQ_RE, SEEN_RE, LENGTH_RE):
        for m in regex.finditer(query):
            if states[m.start()] != (0, False):
                return False
    for m in _OR_TOKEN_RE.finditer(query):
        if states[m.start()] == (0, False):
            return False
    return True


def _call_resolver(resolver, injected, candidate_nids, *args):
    """Invoke a resolver. Injected (test) resolvers keep the original positional
    signature; the real resolvers also accept the candidate-set restriction."""
    if injected:
        return resolver(*args)
    return resolver(*args, candidate_nids=candidate_nids)


def rewrite_query(query, *, occ_resolver=None, freq_resolver=None, kanji_resolver=None, seen_resolver=None, length_resolver=None, find_notes=None) -> str:
    """Replace every custom token in `query` with a concrete `nid:` clause.

    Resolvers are injectable for testing and default to the real ones:
      occ_resolver(dict_str, op, thresh) -> list[int]
      freq_resolver(op, thresh) -> list[int]
      kanji_resolver(check_type, target, op, thresh) -> list[int]
      seen_resolver(n) -> list[int]
      length_resolver(op, thresh) -> list[int]
    Idempotent: the output contains no custom token.
    """
    if not query:
        return query
    has_occ = bool(OCC_RE.search(query))
    has_kanji = bool(KANJI_RE.search(query))
    has_freq = bool(FREQ_RE.search(query))
    has_seen = bool(SEEN_RE.search(query))
    has_length = bool(LENGTH_RE.search(query))
    if not (has_occ or has_kanji or has_freq or has_seen or has_length):
        return query  # fast path: nothing to resolve

    injected = (occ_resolver is not None or freq_resolver is not None
                or kanji_resolver is not None or seen_resolver is not None
                or length_resolver is not None)
    occ = occ_resolver or resolve_occurrences
    freq = freq_resolver or resolve_frequency
    kanji = kanji_resolver or resolve_kanji
    seen = seen_resolver or resolve_seen
    length = length_resolver or resolve_length

    # Restrict resolution to the notes the standard part of the query already
    # selects, so e.g. `deck:X occurrences:D>5` evaluates the occurrence predicate
    # over deck X only instead of the whole collection. Skipped when resolvers are
    # injected (tests pass no collection) or when restriction would be unsafe
    # (see _candidate_restriction_allowed). Those fall back to the full scan.
    candidate_nids = None
    if not injected:
        fn = find_notes if find_notes is not None else _default_find_notes()
        if fn is not None:
            # candidate_base_query drops whole conjuncts, so the custom tokens (including
            # seen:, which the unpatched find_notes cannot parse) and any stray negation go
            # with them. A query offering nothing to restrict on returns None -> full scan.
            base = candidate_base_query(query)
            if base:
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
                kanji, injected, candidate_nids, *_kanji_args(m))),
            query,
        )
    if has_freq:
        query = FREQ_RE.sub(
            lambda m: _format_nid_clause(_call_resolver(
                freq, injected, candidate_nids, m.group("op"), int(m.group("thresh")))),
            query,
        )
    if has_seen:
        def _seen_sub(m):
            n = int(m.group("n"))
            if n <= 0:
                return _format_nid_clause([])  # seen:0 matches nothing
            return _format_nid_clause(_call_resolver(seen, injected, candidate_nids, n))
        query = SEEN_RE.sub(_seen_sub, query)
    if has_length:
        query = LENGTH_RE.sub(
            lambda m: _format_nid_clause(_call_resolver(
                length, injected, candidate_nids, m.group("op"), int(m.group("thresh")))),
            query,
        )
    return query


def _safe_rewrite(query: str) -> str:
    try:
        return rewrite_query(query, find_notes=_default_find_notes())
    except Exception:
        logger.exception("custom-term rewrite failed; passing query through unchanged")
        return query


# Cache of resolved nid lists, keyed by (token_string, collection signature). A single reorder
# run issues many find_cards calls, and repeated browser searches re-resolve the same tokens.
# Correctness first: any collection change (mw.col.mod) OR any addon config change, which does
# not touch the collection, invalidates the whole memo.
_resolution_cache = {}
_resolution_sig = None

# Separate memo for `seen:` full scans. It cannot share _resolution_cache because a seen result
# also depends on today's date and the seen files' mtimes, neither of which _resolution_sig
# captures. Keyed by (n, op, thresh) under a signature that adds both (see resolve_seen).
_seen_cache = {}
_seen_sig = None


def clear_resolution_caches() -> None:
    """Drop every memoized resolution result.

    Needed because both memos are invalidated by *collection* and *config* changes only
    (see the signatures below), and a dictionary update changes neither, since it rewrites
    files on disk. Without this, a Browse-bar `occurrences:` search keeps serving pre-update note
    ids until something happens to touch the collection. Called by the updater alongside the
    dictionary index caches."""
    global _resolution_sig, _seen_sig
    _resolution_cache.clear()
    _resolution_sig = None
    _seen_cache.clear()
    _seen_sig = None


def _config_fingerprint():
    """The addon-config values resolution results depend on (occurrence flags,
    field names, sort_field). Editing the config doesn't bump mw.col.mod, so these
    must be part of the cache signature."""
    try:  # inside Anki: isolated package namespace
        from .config_manager import get_config
    except ImportError:  # pytest / flat-import context
        from config_manager import get_config
    try:
        cfg = get_config()
    except Exception:
        return None
    return (
        cfg.kana_normalization,
        cfg.combine_word_forms,
        cfg.prefix_matching,
        cfg.suffix_matching,
        cfg.variant_matching,
        cfg.stem_matching,
        cfg.compound_matching,
        cfg.honorific_folding,
        cfg.sort_field,
        cfg.search_config.expression_field,
        cfg.search_config.expression_reading_field,
    )


def _resolve(key, compute, candidate_nids):
    """Run a resolver's compute() with the right caching scope.

    Full-scan results (candidate_nids is None) are candidate-independent, so they
    are memoized (and the whole memo is dropped whenever the collection or the
    addon config changes). Restricted results depend on the candidate set, are
    already cheap, and must never be served to a different query, so they are computed
    fresh and unmemoized."""
    if candidate_nids is not None:
        return compute()

    global _resolution_sig
    from aqt import mw

    sig = (mw.col.mod, _config_fingerprint())
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
    from .dictionary_manager import expand_dict_names, occurrence_counter

    def compute():
        cfg = get_config()
        expr_field = cfg.search_config.expression_field
        read_field = cfg.search_config.expression_reading_field
        comparator = parse_comparator(op)
        # Index resolution and flag dispatch hoisted out of the note loop; see
        # dictionary_manager.occurrence_counter.
        count_occurrences = occurrence_counter(
            expand_dict_names(dict_str),
            normalize_kana=cfg.kana_normalization,
            combine_word_forms=cfg.combine_word_forms,
            prefix_matching=cfg.prefix_matching,
            suffix_matching=cfg.suffix_matching,
            variant_matching=cfg.variant_matching,
            stem_matching=cfg.stem_matching,
            compound_matching=cfg.compound_matching,
            honorific_folding=cfg.honorific_folding,
        )

        ids = []
        for nid, values in _iter_candidate_notes((expr_field, read_field), candidate_nids):
            expression = values[expr_field]
            reading = values[read_field]
            if not expression or not reading:
                continue
            if comparator(count_occurrences(expression, reading), thresh):
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


def resolve_length(op, thresh, candidate_nids=None):
    """Note ids whose expression-field length (Unicode code points of the raw
    value, with no HTML stripping or normalization, like every other consumer of the
    field) satisfies the threshold. Unlike resolve_kanji there is deliberately no
    empty-expression skip: an empty field counts as length 0, so `length=0` finds
    it and `length<3` includes it."""
    try:  # inside Anki: isolated package namespace
        from .config_manager import get_config
    except ImportError:  # pytest / flat-import context
        from config_manager import get_config

    def compute():
        cfg = get_config()
        expr_field = cfg.search_config.expression_field
        comparator = parse_comparator(op)

        ids = []
        for nid, values in _iter_candidate_notes((expr_field,), candidate_nids):
            if comparator(len(values[expr_field]), thresh):
                ids.append(nid)
        return ids

    return _resolve(("length", op, thresh), compute, candidate_nids)


def resolve_kanji(check_type, target, op, thresh, candidate_nids=None):
    """Note ids whose expression has the requested kanji count. For "new" and
    "new_reading", `target` is the per-kanji bar. A kanji, or the reading it takes in this
    word, counts as new until `target` learned words contain it
    (1 = the plain form). Notes with an empty expression are skipped (never
    matched), mirroring KanjiRule.matches; "new_reading" additionally needs a
    reading, and skips notes without one like resolve_occurrences does."""
    from .config_manager import get_config
    from .kanji_manager import get_kanji_manager

    def compute():
        cfg = get_config()
        expr_field = cfg.search_config.expression_field
        read_field = cfg.search_config.expression_reading_field
        comparator = parse_comparator(op)
        km = get_kanji_manager(cfg)
        wants_reading = check_type == "new_reading"
        if wants_reading:
            km.enable_readings()
        km.initialize()  # once per batch, not per evaluated note

        # Only new_reading requires the reading field. _iter_candidate_notes
        # skips note types missing any required field, so asking for it
        # unconditionally would silently change what kanji:new/kanji:num match.
        required = (expr_field, read_field) if wants_reading else (expr_field,)

        ids = []
        for nid, values in _iter_candidate_notes(required, candidate_nids):
            expression = values[expr_field]
            if not expression:
                continue
            if wants_reading:
                reading = values[read_field]
                if not reading:
                    continue
                count = km.get_new_reading_count(expression, reading, target)
            elif check_type == "new":
                count = km.get_unknown_kanji_count(expression, target)
            else:  # "num"
                count = km.get_kanji_count(expression)
            if comparator(count, thresh):
                ids.append(nid)
        return ids

    return _resolve(("kanji", check_type, target, op, thresh), compute, candidate_nids)


def resolve_seen(n, candidate_nids=None):
    """Note ids whose word appears in any of the last `n` daily seen dicts
    (user_files/_seen/<date>/), via seen_manager's window, so the global matching flags apply
    just as they do to `occurrences:`.

    Notes with an empty expression are skipped. The reading is optional, falling back to
    expression-only, matching daily-occurrence-search.

    The window is resolved once per call, not per note. Full scans are memoized in
    `_seen_cache` rather than through `_resolve`, because a seen result also depends on today's
    date and the seen files' mtimes, which change without bumping mw.col.mod."""
    try:  # inside Anki: isolated package namespace
        from .config_manager import get_config
        from . import seen_manager
    except ImportError:  # pytest / flat-import context
        from config_manager import get_config
        import seen_manager

    if n <= 0:
        return []

    cfg = get_config()
    expr_field = cfg.search_config.expression_field
    read_field = cfg.search_config.expression_reading_field
    today = seen_manager.today_date()

    def compute():
        # Resolve the window ONCE (one filesystem stat per day), then the note loop is pure
        # in-memory membership lookups. Resolving per note would re-stat the seen folder once
        # per note per day, which is pathologically slow.
        window = seen_manager.get_seen_window(
            n, cfg.kana_normalization, cfg.honorific_folding, cfg.variant_matching,
            cfg.stem_matching, cfg.compound_matching, today=today,
        )
        ids = []
        for nid, values in _iter_candidate_notes((expr_field, read_field), candidate_nids):
            expression = values[expr_field]
            if not expression:
                continue
            reading = values[read_field]
            if window.contains(
                expression,
                reading,
                normalize_kana=cfg.kana_normalization,
                combine_word_forms=cfg.combine_word_forms,
                prefix_matching=cfg.prefix_matching,
                suffix_matching=cfg.suffix_matching,
                variant_matching=cfg.variant_matching,
                stem_matching=cfg.stem_matching,
                compound_matching=cfg.compound_matching,
                honorific_folding=cfg.honorific_folding,
            ):
                ids.append(nid)
        return ids

    # Restricted resolution depends on the candidate set and is already cheap, so it is
    # computed fresh (mirrors _resolve). Full scans recur across the many find_cards calls of
    # a single reorder, because each priority search wraps the query in parens and defeats the
    # candidate restriction, so they are memoized. The key makes date rollover and seen-file
    # rewrites, which don't bump mw.col.mod, still invalidate the result.
    if candidate_nids is not None:
        return compute()

    global _seen_sig
    from aqt import mw

    # The window mtimes belong in the KEY, not the signature: they depend on n, so keying
    # on (n,) under a signature that included them made seen:1 and seen:7 clear each
    # other's entry and rescan every time. What's left in the signature is the part every
    # level shares.
    sig = (mw.col.mod, _config_fingerprint(), today)
    if sig != _seen_sig:
        _seen_cache.clear()
        _seen_sig = sig
    key = (n, seen_manager.window_mtimes(n, today))
    if key not in _seen_cache:
        # Today's dict is rewritten repeatedly while immersing, and each rewrite yields a
        # new key for every window containing today. Drop this level's older entries so a
        # long session replaces them instead of accumulating one dead nid list per save
        # (mirrors the prune in seen_manager.get_seen_window).
        for stale in [k for k in _seen_cache if k[0] == n]:
            del _seen_cache[stale]
        _seen_cache[key] = compute()
    return _seen_cache[key]


_installed = False
_original_find_cards = None
_original_find_notes = None


def _default_find_notes():
    """An unpatched ``find_notes(query) -> [nid]`` bound to the live collection,
    used to compute the candidate set for restricted resolution. Returns ``None``
    when no collection is available (headless pytest, or before a profile opens).

    The stripped query passed to it never contains custom tokens, so going through the
    still-patched ``mw.col.find_notes`` would not recurse. The saved original is preferred
    once it exists, to avoid the rewrite hop entirely."""
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
