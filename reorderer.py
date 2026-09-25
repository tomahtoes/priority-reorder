import itertools
import operator
import time
from typing import List, Optional, Set, Tuple, Dict
from aqt import mw
from anki.collection import OpChangesWithCount

try:  # inside Anki: isolated package namespace
    from .models import Card
    from .config_manager import Config, get_config
    from .data_manager import DataManager
    from .rules import parse_rule_string
    from .search import has_custom_term
    from .reorder_log import (
        PrioritySearchSummary,
        ReorderReport,
        append_timings_line,
        now_timestamp,
        set_last_report,
    )
except ImportError:  # pytest / flat-import context
    from models import Card
    from config_manager import Config, get_config
    from data_manager import DataManager
    from rules import parse_rule_string
    from search import has_custom_term
    from reorder_log import (
        PrioritySearchSummary,
        ReorderReport,
        append_timings_line,
        now_timestamp,
        set_last_report,
    )

# (anki_query, limit). Custom occurrences:/f/kanji: terms stay inside the query
# and are resolved by the patched Collection.find_cards (see search.py).
PriorityDef = Tuple[str, Optional[int]]

# Dev switch: flip to True to also append every reorder's timings line to
# user_files/_timings.log (last 200 runs kept), useful when Anki runs without
# a console. Off by default; normal users never see a file appear.
_DUMP_TIMINGS_LOG = False

# Sort keys for _sort_cards. attrgetter is a C-level call, measurably cheaper than a
# bound method or a lambda over the tens of thousands of cards a reorder sorts.
_SORT_VALUE = operator.attrgetter("data.sort_field_value")
_CARD_ID = operator.attrgetter("card_id")

class PriorityReorderer:
    def __init__(self, config: Config, trigger: str = "manual") -> None:
        self.config = config
        self.data_manager = DataManager(config)
        self.trigger = trigger
        self._promoted_count = 0

    def reorder(self) -> OpChangesWithCount:
        timings: Dict[str, float] = {}
        t_start = time.perf_counter()
        last = t_start

        def mark(stage: str) -> None:
            nonlocal last
            now = time.perf_counter()
            timings[stage] = round((now - last) * 1000, 1)
            last = now

        priority_defs, raw_queries = self._parse_definitions()
        if not priority_defs:
            return OpChangesWithCount(count=0)

        summaries = self._init_summaries(priority_defs, raw_queries)

        priority_matches, all_candidate_ids = self._find_matches(priority_defs, summaries)
        mark("find_matches")

        all_cards_map = self.data_manager.get_cards(all_candidate_ids)
        mark("load_cards")

        priority_buckets, normal_list = self._assign_initial_buckets(priority_defs, priority_matches, all_candidate_ids, all_cards_map)
        mark("buckets")

        final_priority_buckets, final_normal_list = self._apply_refinement_rules(
            priority_buckets, normal_list, summaries
        )
        mark("refine")

        final_priority_queue, overflow = self._finalize_priority_queue(
            priority_defs, final_priority_buckets, summaries
        )
        final_normal_list.extend(overflow)
        mark("finalize")

        result = self._apply_reordering(final_priority_queue, final_normal_list, timings)

        # Sub-stage accumulators from the data manager (find_cards, bulk load,
        # per-term filters, kanji scan, ...). getattr: tests inject bare fakes.
        dm_stage_ms = getattr(self.data_manager, "stage_ms", None)
        if dm_stage_ms:
            for k, v in dm_stage_ms.items():
                timings[k] = round(v, 1)

        timings["total"] = round((time.perf_counter() - t_start) * 1000, 1)
        print("[priority-reorder] timings: " + " ".join(f"{k}={v}ms" for k, v in timings.items()))
        if _DUMP_TIMINGS_LOG:
            append_timings_line(now_timestamp(), timings)

        self._print_reading_diagnostics()

        self._write_log(summaries, final_priority_queue, final_normal_list, result, timings)

        return result

    def _print_reading_diagnostics(self) -> None:
        """Report how much of the collection the kanji reading table could
        explain, when a kanji:new_reading term ran.

        Console only, deliberately. kanji:new_reading is the one term that fails
        by matching too MUCH. A reading field holding markup or the wrong field
        leaves every kanji unexplained, so it matches essentially every card and
        the reorder still reports success. That needs to be *sayable*, but it is
        troubleshooting output, not something to put in front of every user.
        """
        # getattr: tests inject bare data-manager fakes without the method.
        collect = getattr(self.data_manager, "reading_diagnostics", None)
        if collect is None:
            return
        try:
            diagnostics = collect()
        except Exception:
            return
        if not diagnostics:
            return
        warning = diagnostics.pop("new_reading_warning", None)
        if diagnostics:
            print("[priority-reorder] " + " ".join(
                f"{k}={v}" for k, v in diagnostics.items()))
        if warning:
            print("[priority-reorder] warning: " + warning)

    def _parse_definitions(self) -> Tuple[List[PriorityDef], List[str]]:
        priority_defs: List[PriorityDef] = []
        raw_queries: List[str] = []
        try:
            raw = self.config.priority_search
            searches = [raw] if isinstance(raw, str) else (raw or [])
            for s in searches:
                if s.strip():
                    priority_defs.append(parse_rule_string(s))
                    raw_queries.append(s)
        except (ValueError, AttributeError) as e:
            import traceback
            print(f"[priority-reorder] Failed to parse priority_search: {e}")
            traceback.print_exc()
            return [], []
        return priority_defs, raw_queries

    def _init_summaries(self, defs: List[PriorityDef], raw_queries: List[str]) -> List[PrioritySearchSummary]:
        summaries: List[PrioritySearchSummary] = []
        for i, (anki_query, limit) in enumerate(defs):
            summaries.append(PrioritySearchSummary(
                index=i,
                query=raw_queries[i] if i < len(raw_queries) else anki_query,
                anki_query=anki_query,
                has_custom_rules=has_custom_term(anki_query),
                limit=limit,
            ))
        return summaries

    def _find_matches(
        self,
        priority_defs: List[PriorityDef],
        summaries: List[PrioritySearchSummary],
    ) -> Tuple[Dict[int, Set[int]], Set[int]]:
        priority_matches: Dict[int, Set[int]] = {}
        all_ids: Set[int] = set()

        for i, (anki_query, _) in enumerate(priority_defs):
            # get_cards_from_search returns the already-filtered match set: a
            # conjunctive custom-term query is resolved by evaluating the standard
            # part once and post-filtering the custom terms in Python; only
            # disjunctive/grouped queries fall through to the patched find_cards.
            result = self.data_manager.get_cards_from_search(anki_query)

            matched_ids = {c.card_id for c in result.cards}
            priority_matches[i] = matched_ids
            summaries[i].raw_match_count = result.raw_count
            summaries[i].refined_match_count = len(matched_ids)
            all_ids.update(matched_ids)

        normal_cards = self.data_manager.get_cards_from_search(self.config.normal_search).cards
        all_ids.update(c.card_id for c in normal_cards)

        return priority_matches, all_ids

    def _assign_initial_buckets(self, defs: List[PriorityDef], matches: Dict[int, Set[int]], all_ids: Set[int], card_map: Dict[int, Card]) -> Tuple[List[List[Card]], List[Card]]:
        priority_buckets = []
        matched_ids = set().union(*matches.values())

        if self.config.priority_search_mode == "mix":
            bucket = [card_map[cid] for cid in matched_ids if cid in card_map]
            priority_buckets.append(bucket)
        else:
            for i in range(len(defs)):
                ids = matches[i]
                bucket = [card_map[cid] for cid in ids if cid in card_map]
                priority_buckets.append(bucket)

        normal_ids = all_ids - matched_ids
        normal_list = [card_map[cid] for cid in normal_ids if cid in card_map]

        return priority_buckets, normal_list

    def _apply_refinement_rules(
        self,
        priority_buckets: List[List[Card]],
        normal_list: List[Card],
        summaries: List[PrioritySearchSummary],
    ) -> Tuple[List[List[Card]], List[Card]]:
        cutoff = self.config.priority_cutoff
        prioritization = self.config.normal_prioritization
        reverse = self.config.sort_reverse

        def split_by_threshold(cards: List[Card], threshold: Optional[int]) -> Tuple[List[Card], List[Card]]:
            """Partition into (over, rest) by the sort value exceeding `threshold` in the
            configured direction. A None threshold puts everything in rest.

            `over` is the "worse" side at both call sites, so a card with no usable sort value
            always belongs there, matching _sort_cards. Testing the raw +inf sentinel instead
            got that right only under reverse=False: under reverse=True `inf < threshold` is
            False, so value-less cards survived the cutoff AND were promoted into priority."""
            if threshold is None:
                return [], list(cards)
            over: List[Card] = []
            rest: List[Card] = []
            for card in cards:
                data = card.data
                if not data.has_sort_value:
                    exceeds = True
                else:
                    val = data.sort_field_value
                    exceeds = val < threshold if reverse else val > threshold
                (over if exceeds else rest).append(card)
            return over, rest

        is_mix = self.config.priority_search_mode == "mix"

        final_priority = []
        final_normal = list(normal_list)

        for bucket_idx, bucket in enumerate(priority_buckets):
            dropped, kept = split_by_threshold(bucket, cutoff)
            final_normal.extend(dropped)
            if dropped and not is_mix and bucket_idx < len(summaries):
                summaries[bucket_idx].cutoff_dropped += len(dropped)
                summaries[bucket_idx].cutoff_note_ids.extend(c.note_id for c in dropped)
            final_priority.append(kept)

        # Promote low-value normal cards into their own dedicated trailing tier.
        # Kept separate from the real search buckets so they are exempt from any
        # single search's per-search limit and excluded from per-search summaries. In
        # mix mode the tier is flattened into the single sorted pool later, so
        # promoted cards interleave with priority matches by sort value instead
        # of trailing them.
        if prioritization is None:
            new_normal, promoted = final_normal, []
        else:
            new_normal, promoted = split_by_threshold(final_normal, prioritization)
        self._promoted_count = len(promoted)
        if promoted:
            final_priority.append(promoted)

        return final_priority, new_normal

    def _finalize_priority_queue(
        self,
        defs: List[PriorityDef],
        buckets: List[List[Card]],
        summaries: List[PrioritySearchSummary],
    ) -> Tuple[List[Card], List[Card]]:
        queue: List[Card] = []
        overflow: List[Card] = []

        is_mix = self.config.priority_search_mode == "mix"

        if is_mix:
            flat = [c for b in buckets for c in b]
            queue = self._sort_cards(flat)
        else:
            seen: Set[int] = set()
            all_priority_cards = {c.card_id: c for b in buckets for c in b}

            bucket_kept_cards: Dict[int, List[Card]] = {i: [] for i in range(len(buckets))}

            for i, bucket in enumerate(buckets):
                eligible = [c for c in bucket if c.card_id not in seen]
                if i < len(summaries):
                    summaries[i].overlap_count += len(bucket) - len(eligible)
                sorted_bucket = self._sort_cards(eligible)

                limit = defs[i][1] if i < len(defs) else None
                if limit is not None:
                    taken = sorted_bucket[:limit]
                    discarded = sorted_bucket[limit:]
                    for card in taken:
                        queue.append(card)
                        seen.add(card.card_id)
                        bucket_kept_cards[i].append(card)
                    if i < len(summaries):
                        summaries[i].limit_discarded += len(discarded)
                        summaries[i].discarded_note_ids.extend(c.note_id for c in discarded)
                else:
                    for card in sorted_bucket:
                        queue.append(card)
                        seen.add(card.card_id)
                        bucket_kept_cards[i].append(card)

            # Matched priority but landed in no bucket: sequential dedup already counted
            # them in an earlier bucket.
            for cid, card in all_priority_cards.items():
                if cid not in seen:
                    overflow.append(card)

        global_limit = self.config.priority_limit
        global_overflow: List[Card] = []
        if global_limit is not None and len(queue) > global_limit:
            global_overflow = queue[global_limit:]
            queue = queue[:global_limit]
            overflow.extend(global_overflow)

        if not is_mix:
            kept_set = {c.card_id for c in queue}
            for i in range(len(buckets)):
                if i >= len(summaries):
                    continue
                for c in bucket_kept_cards.get(i, []):
                    if c.card_id in kept_set:
                        summaries[i].kept_note_ids.append(c.note_id)
                    else:
                        summaries[i].global_limit_discarded += 1
                        summaries[i].discarded_note_ids.append(c.note_id)
                summaries[i].kept_count = len(summaries[i].kept_note_ids)

            # Final start index in the reordered queue (sequential mode):
            # priority cards occupy positions 0..N-1, each search a contiguous
            # block, so its start = cumulative kept of preceding searches.
            cumulative = 0
            for i in range(len(buckets)):
                if i >= len(summaries):
                    continue
                if summaries[i].kept_count > 0:
                    summaries[i].final_start_index = cumulative
                    cumulative += summaries[i].kept_count
                # searches with 0 kept leave final_start_index = None

        return queue, overflow

    def _apply_reordering(
        self,
        priority_queue: List[Card],
        normal_list: List[Card],
        timings: Optional[Dict[str, float]] = None,
    ) -> OpChangesWithCount:
        def mark(stage: str, since: float) -> float:
            now = time.perf_counter()
            if timings is not None:
                timings[stage] = round((now - since) * 1000, 1)
            return now

        t = time.perf_counter()
        normal_list = self._sort_cards(normal_list)

        final_ids = []
        seen = set()

        for card in itertools.chain(priority_queue, normal_list):
            if card.card_id not in seen:
                final_ids.append(card.card_id)
                seen.add(card.card_id)
        t = mark("final_sort", t)

        if not final_ids:
            return OpChangesWithCount(count=0)

        needs = self._needs_reorder(final_ids)
        t = mark("needs_reorder", t)
        if not needs:
            return OpChangesWithCount(count=0)

        result = mw.col.sched.reposition_new_cards(
            card_ids=final_ids,
            starting_from=0,
            step_size=1,
            randomize=False,
            shift_existing=self.config.shift_existing
        )
        mark("reposition", t)
        return result

    def _needs_reorder(self, new_ids: List[int]) -> bool:
        """Whether repositioning would actually change the new-card order.

        Anki's reposition rewrites every new card it touches, and under shift_existing bumps
        every position by a fixed offset unconditionally, even when the resulting order is
        identical. That marks the cards for sync, leaving the sync button stuck on "changes
        pending". Skipping the reposition is the only way to avoid that churn.

        The two shift_existing modes need different tests, because they write different things.
        """
        if not new_ids:
            return False

        if not self.config.shift_existing:
            # Without shift_existing, reposition writes due = index for exactly these
            # cards and moves nothing else. So "already applied" is a per-card test,
            # and it MUST be: comparing against the global new-card order churns
            # forever on a new card outside every configured search whose due happens
            # to land inside 0..N-1. Nothing can move it, so the order never matches
            # and every reorder re-dirties the whole backlog for sync.
            #
            # Bounded by `due < N` rather than inlining the ids: a 100k-card backlog
            # would blow past SQLite's statement-length limit (see _BULK_CHUNK_SIZE).
            try:
                due_by_id = dict(mw.col.db.all(
                    f"select id, due from cards where type = 0 and due < {len(new_ids)}"
                ))
            except Exception:
                return True
            return any(due_by_id.get(cid) != i for i, cid in enumerate(new_ids))

        try:
            # type = 0 == new cards (the `is:new` domain every search uses).
            # Tie-break by id so equal-due cards order deterministically across
            # runs; otherwise a due collision could flip the order and trigger a
            # needless reorder. Only the first len(new_ids) positions are compared
            # below, so the scan is capped there; getting fewer rows than the cap
            # still means fewer new cards exist than we are placing -> reorder.
            current_ids = mw.col.db.list(
                f"select id from cards where type = 0 order by due, id limit {len(new_ids)}"
            )
        except Exception:
            # The comparison is what decides whether a reorder can be skipped, so a failed
            # query must fall back to reordering rather than to skipping.
            return True

        if len(current_ids) < len(new_ids):
            return True

        return current_ids[:len(new_ids)] != new_ids

    def _sort_cards(self, cards: List[Card]) -> List[Card]:
        # Cards with no usable sort value always trail, in either sort direction
        # (a single numeric sentinel can't do this: reverse=True would float it
        # to the top).
        present: List[Card] = []
        missing: List[Card] = []
        for card in cards:
            (present if card.data.has_sort_value else missing).append(card)

        # Equal sort values (and the whole `missing` group) used to keep their input
        # order, which traces back to iterating sets of card ids in
        # _assign_initial_buckets, an order that shifts when the card set changes.
        # A handful of cards graduating could then permute a tie group, which is
        # exactly what makes _needs_reorder see a different order and reposition the
        # whole backlog for nothing. Breaking ties on card id makes the produced order
        # reproducible and matches _needs_reorder's own `order by due, id`.
        #
        # Two stable passes rather than a (value, id) tuple key: reverse=True would
        # also reverse the id component, and the tuple key measured slower than both
        # attrgetter sorts combined.
        present.sort(key=_CARD_ID)
        present.sort(key=_SORT_VALUE, reverse=self.config.sort_reverse)
        missing.sort(key=_CARD_ID)
        return present + missing

    def _write_log(
        self,
        summaries: List[PrioritySearchSummary],
        final_priority_queue: List[Card],
        final_normal_list: List[Card],
        result: OpChangesWithCount,
        timings: Optional[Dict[str, float]] = None,
    ) -> None:
        report: Optional[ReorderReport] = None
        try:
            report = ReorderReport(
                timestamp=now_timestamp(),
                mode=self.config.priority_search_mode,
                priority_cutoff=self.config.priority_cutoff,
                global_priority_limit=self.config.priority_limit,
                entries=summaries,
                total_priority_kept=len(final_priority_queue),
                total_normal=len(final_normal_list),
                total_repositioned=getattr(result, "count", 0) or 0,
                timings_ms=dict(timings or {}),
                promoted_count=self._promoted_count,
                trigger=self.trigger,
            )
        except Exception as e:
            import traceback
            print(f"[priority-reorder] Failed to build reorder report: {e}")
            traceback.print_exc()
            return

        try:
            set_last_report(report)
        except Exception as e:
            import traceback
            print(f"[priority-reorder] Failed to store in-memory report: {e}")
            traceback.print_exc()

def run_reorder(col=None, trigger: str = "manual") -> OpChangesWithCount:
    if mw.col is None:
        return OpChangesWithCount(count=0)
    return PriorityReorderer(get_config(), trigger=trigger).reorder()
