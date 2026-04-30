from typing import List, Optional, Set, Tuple, Dict
from aqt import mw
from anki.collection import OpChangesWithCount

from .models import Card
from .config_manager import Config, get_config
from .data_manager import DataManager
from .kanji_manager import get_kanji_manager
from .rules import parse_rule_string, Rule
from .reorder_log import (
    PrioritySearchStats,
    ReorderReport,
    now_timestamp,
    set_last_report,
)

PriorityDef = Tuple[List[Rule], str, Optional[int]]

class PriorityReorderer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.data_manager = DataManager(config)
        self.kanji_manager = get_kanji_manager(config)

    def reorder(self) -> OpChangesWithCount:
        priority_defs, raw_queries = self._parse_definitions()
        if not priority_defs:
            return OpChangesWithCount(count=0)

        stats = self._init_stats(priority_defs, raw_queries)

        priority_matches, all_candidate_ids, card_id_to_note = self._find_matches(priority_defs, stats)

        all_cards_map = {cid: self.data_manager.get_card(cid) for cid in all_candidate_ids}
        all_cards_map = {k: v for k, v in all_cards_map.items() if v is not None}
        for cid, card in all_cards_map.items():
            card_id_to_note[cid] = card.note_id

        priority_buckets, normal_list = self._assign_initial_buckets(priority_defs, priority_matches, all_candidate_ids, all_cards_map)

        # Track which bucket each card originally belonged to (for sequential mode attribution)
        card_origin: Dict[int, int] = {}
        if self.config.priority_search_mode != "mix":
            for i, bucket in enumerate(priority_buckets):
                for c in bucket:
                    card_origin.setdefault(c.card_id, i)

        final_priority_buckets, final_normal_list = self._apply_refinement_rules(
            priority_buckets, normal_list, stats, card_origin
        )
        final_priority_queue, overflow = self._finalize_priority_queue(
            priority_defs, final_priority_buckets, stats, card_origin
        )
        final_normal_list.extend(overflow)

        result = self._apply_reordering(final_priority_queue, final_normal_list)

        self._write_log(stats, final_priority_queue, final_normal_list, result)

        return result

    def _parse_definitions(self) -> Tuple[List[PriorityDef], List[str]]:
        priority_defs: List[PriorityDef] = []
        raw_queries: List[str] = []
        try:
            raw = self.config.priority_search
            searches = [raw] if isinstance(raw, str) else (raw or [])
            for s in searches:
                if s.strip():
                    priority_defs.append(parse_rule_string(
                        s,
                        kanji_manager=self.kanji_manager,
                        normalize_kana=self.config.kana_normalization,
                        combine_word_forms=self.config.combine_word_forms,
                        prefix_matching=self.config.prefix_matching,
                        honorific_folding=self.config.honorific_folding
                    ))
                    raw_queries.append(s)
        except (ValueError, AttributeError) as e:
            import traceback
            print(f"[priority-reorder] Failed to parse priority_search: {e}")
            traceback.print_exc()
            return [], []
        return priority_defs, raw_queries

    def _init_stats(self, defs: List[PriorityDef], raw_queries: List[str]) -> List[PrioritySearchStats]:
        stats: List[PrioritySearchStats] = []
        for i, (rules, anki_query, limit) in enumerate(defs):
            stats.append(PrioritySearchStats(
                index=i,
                query=raw_queries[i] if i < len(raw_queries) else anki_query,
                anki_query=anki_query,
                has_custom_rules=bool(rules),
                limit=limit,
            ))
        return stats

    def _find_matches(
        self,
        priority_defs: List[PriorityDef],
        stats: List[PrioritySearchStats],
    ) -> Tuple[Dict[int, Set[int]], Set[int], Dict[int, int]]:
        priority_matches: Dict[int, Set[int]] = {}
        all_ids: Set[int] = set()
        card_id_to_note: Dict[int, int] = {}

        for i, (rules, anki_query, _) in enumerate(priority_defs):
            cards = self.data_manager.get_cards_from_search(anki_query)
            for c in cards:
                card_id_to_note[c.card_id] = c.note_id

            stats[i].raw_match_count = len(cards)

            refined_ids = {c.card_id for c in cards if all(r.matches(c) for r in rules)}
            priority_matches[i] = refined_ids
            stats[i].refined_match_count = len(refined_ids)
            all_ids.update(refined_ids)

        normal_cards = self.data_manager.get_cards_from_search(self.config.normal_search)
        for c in normal_cards:
            card_id_to_note[c.card_id] = c.note_id
        all_ids.update(c.card_id for c in normal_cards)

        return priority_matches, all_ids, card_id_to_note

    def _assign_initial_buckets(self, defs: List[PriorityDef], matches: Dict[int, Set[int]], all_ids: Set[int], card_map: Dict[int, Card]) -> Tuple[List[List[Card]], List[Card]]:
        priority_buckets = []

        if self.config.priority_search_mode == "mix":
            combined_ids = set().union(*matches.values())
            bucket = [card_map[cid] for cid in combined_ids if cid in card_map]
            priority_buckets.append(bucket)
        else:
            for i in range(len(defs)):
                ids = matches[i]
                bucket = [card_map[cid] for cid in ids if cid in card_map]
                priority_buckets.append(bucket)

        normal_ids = all_ids - set().union(*matches.values())
        normal_list = [card_map[cid] for cid in normal_ids if cid in card_map]

        return priority_buckets, normal_list

    def _apply_refinement_rules(
        self,
        priority_buckets: List[List[Card]],
        normal_list: List[Card],
        stats: List[PrioritySearchStats],
        card_origin: Dict[int, int],
    ) -> Tuple[List[List[Card]], List[Card]]:
        cutoff = self.config.priority_cutoff
        prioritization = self.config.normal_prioritization
        reverse = self.config.sort_reverse

        final_priority = []
        final_normal = list(normal_list)

        def exceeds_threshold(card, threshold):
            val = card.data.sort_field_value
            return val < threshold if reverse else val > threshold

        is_mix = self.config.priority_search_mode == "mix"

        for bucket_idx, bucket in enumerate(priority_buckets):
            kept = []
            for card in bucket:
                if cutoff is not None and exceeds_threshold(card, cutoff):
                    final_normal.append(card)
                    if not is_mix and bucket_idx < len(stats):
                        stats[bucket_idx].cutoff_dropped += 1
                        stats[bucket_idx].cutoff_note_ids.append(card.note_id)
                else:
                    kept.append(card)
            final_priority.append(kept)

        # Filter normal list (Prioritization)
        new_normal = []
        for card in final_normal:
            if prioritization is not None and not exceeds_threshold(card, prioritization):
                if not final_priority:
                    final_priority.append([])
                final_priority[-1].append(card)
            else:
                new_normal.append(card)

        return final_priority, new_normal

    def _finalize_priority_queue(
        self,
        defs: List[PriorityDef],
        buckets: List[List[Card]],
        stats: List[PrioritySearchStats],
        card_origin: Dict[int, int],
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

            # Track per-bucket kept ids before global-limit clipping
            bucket_kept_cards: Dict[int, List[Card]] = {i: [] for i in range(len(buckets))}

            for i, bucket in enumerate(buckets):
                eligible = [c for c in bucket if c.card_id not in seen]
                sorted_bucket = self._sort_cards(eligible)

                limit = defs[i][2] if i < len(defs) else None
                if limit is not None:
                    taken = sorted_bucket[:limit]
                    discarded = sorted_bucket[limit:]
                    for card in taken:
                        queue.append(card)
                        seen.add(card.card_id)
                        bucket_kept_cards[i].append(card)
                    if i < len(stats):
                        stats[i].limit_discarded += len(discarded)
                        stats[i].discarded_note_ids.extend(c.note_id for c in discarded)
                else:
                    for card in sorted_bucket:
                        queue.append(card)
                        seen.add(card.card_id)
                        bucket_kept_cards[i].append(card)

            # Cards that matched priority but didn't make it into any bucket
            # (sequential dedup — already counted in earlier bucket; ignored here)
            for cid, card in all_priority_cards.items():
                if cid not in seen:
                    overflow.append(card)

        # Apply global priority limit
        global_limit = self.config.priority_limit
        global_overflow: List[Card] = []
        if global_limit is not None and len(queue) > global_limit:
            global_overflow = queue[global_limit:]
            queue = queue[:global_limit]
            overflow.extend(global_overflow)

        # Record final kept/global-discarded note ids per bucket (sequential mode)
        if not is_mix:
            kept_set = {c.card_id for c in queue}
            for i in range(len(buckets)):
                if i >= len(stats):
                    continue
                for c in bucket_kept_cards.get(i, []):
                    if c.card_id in kept_set:
                        stats[i].kept_note_ids.append(c.note_id)
                    else:
                        stats[i].global_limit_discarded += 1
                        stats[i].discarded_note_ids.append(c.note_id)
                stats[i].kept_count = len(stats[i].kept_note_ids)

        return queue, overflow

    def _apply_reordering(self, priority_queue: List[Card], normal_list: List[Card]) -> OpChangesWithCount:
        normal_list = self._sort_cards(normal_list)

        final_ids = []
        seen = set()

        for card in priority_queue + normal_list:
            if card.card_id not in seen:
                final_ids.append(card.card_id)
                seen.add(card.card_id)

        if not final_ids:
            return OpChangesWithCount(count=0)

        return mw.col.sched.reposition_new_cards(
            card_ids=final_ids,
            starting_from=0,
            step_size=1,
            randomize=False,
            shift_existing=self.config.shift_existing
        )

    def _get_sort_key(self, card: Card) -> float:
        return card.data.sort_field_value

    def _sort_cards(self, cards: List[Card]) -> List[Card]:
        return sorted(cards, key=self._get_sort_key, reverse=self.config.sort_reverse)

    def _write_log(
        self,
        stats: List[PrioritySearchStats],
        final_priority_queue: List[Card],
        final_normal_list: List[Card],
        result: OpChangesWithCount,
    ) -> None:
        report: Optional[ReorderReport] = None
        try:
            report = ReorderReport(
                timestamp=now_timestamp(),
                mode=self.config.priority_search_mode,
                priority_cutoff=self.config.priority_cutoff,
                global_priority_limit=self.config.priority_limit,
                entries=stats,
                total_priority_kept=len(final_priority_queue),
                total_normal=len(final_normal_list),
                total_repositioned=getattr(result, "count", 0) or 0,
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

def run_reorder(col=None) -> OpChangesWithCount:
    return PriorityReorderer(get_config()).reorder()
