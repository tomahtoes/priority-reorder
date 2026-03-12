from typing import List, Set, Tuple, Dict
from aqt import mw
from anki.collection import OpChangesWithCount

from .models import Card
from .config_manager import Config, get_config
from .data_manager import DataManager
from .kanji_manager import get_kanji_manager
from .rules import parse_rule_string, Rule

class PriorityReorderer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.data_manager = DataManager(config)
        self.kanji_manager = get_kanji_manager(config)

    def reorder(self) -> OpChangesWithCount:
        priority_defs = self._parse_definitions()
        if not priority_defs:
            return OpChangesWithCount(count=0)

        priority_matches, all_candidate_ids = self._find_matches(priority_defs)
        
        # Load all candidate cards
        all_cards_map = {cid: self.data_manager.get_card(cid) for cid in all_candidate_ids}
        all_cards_map = {k: v for k, v in all_cards_map.items() if v is not None}

        priority_buckets, normal_list = self._assign_initial_buckets(priority_defs, priority_matches, all_candidate_ids, all_cards_map)
        final_priority_buckets, final_normal_list = self._apply_refinement_rules(priority_buckets, normal_list)
        final_priority_queue, overflow = self._finalize_priority_queue(priority_defs, final_priority_buckets)
        final_normal_list.extend(overflow)
        
        # Merge, deduplicate, and reposition
        return self._apply_reordering(final_priority_queue, final_normal_list)

    def _parse_definitions(self) -> List[Tuple[List[Rule], str, int | None]]:
        priority_defs = []
        try:
            raw = self.config.priority_search
            searches = [raw] if isinstance(raw, str) else (raw or [])
            for s in searches:
                if s.strip():
                    priority_defs.append(parse_rule_string(
                        s, 
                        kanji_manager=self.kanji_manager
                    ))
        except Exception:
            return []
        return priority_defs

    def _find_matches(self, priority_defs: List[Tuple]) -> Tuple[Dict[int, Set[int]], Set[int]]:
        priority_matches = {}
        all_ids = set()
        
        for i, (rules, anki_query, _) in enumerate(priority_defs):
            cards = self.data_manager.get_cards_from_search(anki_query)
            
            refined_ids = {c.card_id for c in cards if all(r.matches(c) for r in rules)}
            priority_matches[i] = refined_ids
            all_ids.update(refined_ids)

        normal_cards = self.data_manager.get_cards_from_search(self.config.normal_search)
        all_ids.update(c.card_id for c in normal_cards)
        
        return priority_matches, all_ids

    def _assign_initial_buckets(self, defs: List[Tuple], matches: Dict[int, Set[int]], all_ids: Set[int], card_map: Dict[int, Card]) -> Tuple[List[List[Card]], List[Card]]:
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

    def _apply_refinement_rules(self, priority_buckets: List[List[Card]], normal_list: List[Card]) -> Tuple[List[List[Card]], List[Card]]:
        cutoff = self.config.priority_cutoff
        prioritization = self.config.normal_prioritization
        reverse = self.config.sort_reverse
        
        final_priority = []
        final_normal = list(normal_list)

        def exceeds_threshold(card, threshold):
            val = card.data.sort_field_value
            return val < threshold if reverse else val > threshold

        # Filter priority buckets (Cutoff)
        for bucket in priority_buckets:
            kept = []
            for card in bucket:
                if cutoff is not None and exceeds_threshold(card, cutoff):
                    final_normal.append(card)
                else:
                    kept.append(card)
            final_priority.append(kept)

        # Filter normal list (Prioritization)
        new_normal = []
        for card in final_normal:
            if prioritization is not None and not exceeds_threshold(card, prioritization):
                # Move to priority (append to last bucket or create new)
                if not final_priority:
                    final_priority.append([])
                final_priority[-1].append(card)
            else:
                new_normal.append(card)
                
        return final_priority, new_normal

    def _finalize_priority_queue(self, defs, buckets) -> Tuple[List[Card], List[Card]]:
        queue = []
        overflow = []
        
        if self.config.priority_search_mode == "mix":
            flat = [c for b in buckets for c in b]
            queue = self._sort_cards(flat)
        else:
            seen = set()
            all_priority_cards = {c.card_id: c for b in buckets for c in b}
            
            for i, bucket in enumerate(buckets):
                # Filter to cards that match this bucket and haven't been placed yet
                eligible = [c for c in bucket if c.card_id not in seen]
                sorted_bucket = self._sort_cards(eligible)
                
                limit = defs[i][2] if i < len(defs) else None
                if limit is not None:
                    # Take only up to limit; rest can fall through to later buckets
                    for card in sorted_bucket[:limit]:
                        queue.append(card)
                        seen.add(card.card_id)
                else:
                    for card in sorted_bucket:
                        queue.append(card)
                        seen.add(card.card_id)
            
            # Cards that matched priority but didn't make it into any bucket go to overflow
            for cid, card in all_priority_cards.items():
                if cid not in seen:
                    overflow.append(card)

        # Apply Global Limit
        global_limit = self.config.priority_limit
        if global_limit is not None and len(queue) > global_limit:
            overflow.extend(queue[global_limit:])
            queue = queue[:global_limit]
            
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

        if not self._needs_reorder(final_ids):
            return OpChangesWithCount(count=0)

        return mw.col.sched.reposition_new_cards(
            card_ids=final_ids,
            starting_from=0,
            step_size=1,
            randomize=False,
            shift_existing=self.config.shift_existing
        )

    def _needs_reorder(self, new_ids: List[int]) -> bool:
        if not new_ids:
            return False

        try:
            # type = 0 is "new" cards only
            current_ids = mw.col.db.list("select id from cards where type = 0 order by due")
        except Exception:
            # Even though we can't know for sure, default to reordering to match historical behavior
            return True

        if len(current_ids) < len(new_ids):
            return True

        return current_ids[:len(new_ids)] != new_ids

    def _get_sort_key(self, card: Card) -> float:
        return card.data.sort_field_value

    def _sort_cards(self, cards: List[Card]) -> List[Card]:
        return sorted(cards, key=self._get_sort_key, reverse=self.config.sort_reverse)

def run_reorder(col=None) -> OpChangesWithCount:
    return PriorityReorderer(get_config()).reorder()
