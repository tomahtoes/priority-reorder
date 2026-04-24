from abc import ABC, abstractmethod
from typing import List, Tuple
import re
from .models import Card
from .utils import parse_comparator, to_hiragana
from .dictionary_manager import get_occurrence_index, get_combined_occurrence_index, get_all_dict_names
from .kanji_manager import KanjiManager

OCC_PATTERN = re.compile(r"occurrences:(?P<dict>[^=<>!]+)(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)")
FREQ_PATTERN = re.compile(r"^f(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)$")
KANJI_PATTERN = re.compile(r"kanji:(?P<type>new|num)(?P<op>>=|<=|!=|=|<|>)(?P<thresh>\d+)")
LIMIT_PATTERN = re.compile(r"limit=(?P<limit>\d+)")

class Rule(ABC):
    @abstractmethod
    def matches(self, card: Card) -> bool:
        pass

class OccurrenceRule(Rule):
    def __init__(self, dict_names: List[str], operator_str: str, threshold: int, normalize_kana: bool = False, combine_word_forms: bool = False, prefix_matching: bool = False, honorific_folding: bool = False) -> None:
        self.dict_names = dict_names
        self.comparator = parse_comparator(operator_str)
        self.threshold = threshold
        self.normalize_kana = normalize_kana
        self.combine_word_forms = combine_word_forms
        self.prefix_matching = prefix_matching
        self.honorific_folding = honorific_folding

    def matches(self, card: Card) -> bool:
        expression = card.data.expression
        reading = card.data.reading
        if not expression or not reading:
            return False

        if self.normalize_kana:
            expression = to_hiragana(expression)
            reading = to_hiragana(reading)

        if len(self.dict_names) == 1:
            index = get_occurrence_index(self.dict_names[0], self.normalize_kana, self.prefix_matching, self.honorific_folding)
            count = index.get_total(
                expression,
                reading,
                combine_word_forms=self.combine_word_forms,
                prefix_matching=self.prefix_matching,
                honorific_folding=self.honorific_folding,
            )
        else:
            combined_index = get_combined_occurrence_index(tuple(self.dict_names), self.normalize_kana, self.combine_word_forms, self.prefix_matching, self.honorific_folding)
            count = combined_index.get(expression, reading)

        return self.comparator(count, self.threshold)

class FrequencyRule(Rule):
    def __init__(self, operator_str: str, threshold: int) -> None:
        self.comparator = parse_comparator(operator_str)
        self.threshold = threshold

    def matches(self, card: Card) -> bool:
        return self.comparator(card.data.sort_field_value, self.threshold)

class KanjiRule(Rule):
    def __init__(self, check_type: str, operator_str: str, threshold: int, kanji_manager: KanjiManager) -> None:
        self.check_type = check_type
        self.comparator = parse_comparator(operator_str)
        self.threshold = threshold
        self.kanji_manager = kanji_manager

    def matches(self, card: Card) -> bool:
        expression = card.data.expression
        if not expression:
            return False
            
        if self.check_type == "new":
            count = self.kanji_manager.get_unknown_kanji_count(expression)
            return self.comparator(count, self.threshold)

        elif self.check_type == "num":
            count = self.kanji_manager.get_kanji_count(expression)
            return self.comparator(count, self.threshold)
        return False

def parse_rule_string(rule_string: str, kanji_manager: KanjiManager = None, normalize_kana: bool = False, combine_word_forms: bool = False, prefix_matching: bool = False, honorific_folding: bool = False) -> Tuple[List[Rule], str, int | None]:
    rules = []
    limit = None
    
    m_limit = LIMIT_PATTERN.search(rule_string)
    if m_limit:
        limit = int(m_limit.group("limit"))
        rule_string = LIMIT_PATTERN.sub("", rule_string)
        
    tokens = rule_string.split()
    anki_search_tokens = []
    
    for token in tokens:
        m_occ = OCC_PATTERN.match(token)
        if m_occ:
            dict_str = m_occ.group("dict")
            op = m_occ.group("op")
            thresh = int(m_occ.group("thresh"))
            
            if dict_str.startswith('[') and dict_str.endswith(']'):
                raw_names = [d.strip() for d in dict_str[1:-1].split(',')]
            else:
                raw_names = [dict_str]
                
            dict_names = []
            for name in raw_names:
                if name == "all":
                    dict_names.extend(get_all_dict_names())
                else:
                    dict_names.append(name)
            
            # Remove duplicates to act as true combinator
            dict_names = list(dict.fromkeys(dict_names))
                
            rules.append(OccurrenceRule(dict_names, op, thresh, normalize_kana=normalize_kana, combine_word_forms=combine_word_forms, prefix_matching=prefix_matching, honorific_folding=honorific_folding))
            continue
            
        m_freq = FREQ_PATTERN.match(token)
        if m_freq:
            op = m_freq.group("op")
            thresh = int(m_freq.group("thresh"))
            rules.append(FrequencyRule(op, thresh))
            continue

        m_kanji = KANJI_PATTERN.match(token)
        if m_kanji and kanji_manager:
            k_type = m_kanji.group("type")
            op = m_kanji.group("op")
            thresh = int(m_kanji.group("thresh"))
            rules.append(KanjiRule(k_type, op, thresh, kanji_manager))
            continue
            
        anki_search_tokens.append(token)
    
    return rules, " ".join(anki_search_tokens), limit
