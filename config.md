# Priority Reorder Config

Options are grouped into sections: the searches and sorting you tune day to day stay at the
top level, and the rest live under `matching`, `tuning`, `sync_behavior` and `word_fields`.
Headings below give each option's full path, e.g. `matching.prefix_matching` means:

```
"matching": { "prefix_matching": true }
```

## Core Settings

### `priority_search` (string | list)
- **Description**: The Anki search query used to identify cards for the Priority Queue. These cards will always be shown before the "Normal Queue". It can be a single string or a list of multiple search queries.
- **Support**: Supports standard Anki syntax plus custom filters like `kanji:new=1`, `kanji:new[3]>=1`, `kanji:new_reading>=1`, `kanji:num=2`, `f<10000`, `length>=3`, or `occurrences:dict>5`.
- **Default**: `""`
- **Example**: `"deck:Japanese added:3"`

### `priority_search_mode` (string)
- **Description**: Determines how cards are handled when `priority_search` is a list of multiple queries.
- **Options**:
    - `"sequential"`: Processes each search in order. Cards matching the first search appear first, followed by the second, and so on.
    - `"mix"`: Combines all cards from all priority searches into one big group before sorting.
- **Default**: `"sequential"`

### `normal_search` (string)
- **Description**: The Anki search query for your secondary group of cards. These are shown only after all priority cards have been scheduled.
- **Note**: The addon automatically appends `is:new` to all searches to ensure only new cards are affected.
- **Default**: `""`

### `sort_field` (string), **Required**
- **Description**: The name of the field on your Note Type used for numeric sorting (e.g. `"FreqSort"`, `"Frequency"`).
- **Default**: `"FreqSort"`

### `sort_reverse` (bool)
- **Description**: Controls the sorting direction of the `sort_field`.
- **Behavior**: 
    - `false` (Ascending): Lowest values first
    - `true` (Descending): Highest values first
- **Default**: `false`

---

## Tuning (`tuning`)

### `tuning.priority_cutoff` (int | null)
- **Description**: A threshold used to bump cards from the priority queue.
- **Behavior**: If a priority card's sort value exceeds this number, it is moved to the Normal Queue.
- **Multi-search**: Applied to each priority bucket separately. Cards bumped from any bucket go to the normal list.
- **Note**: If the top-level `sort_reverse` is `true`, cards with values *below* the cutoff are moved instead.
- **Default**: `null`

### `tuning.normal_prioritization` (int | null)
- **Description**: A threshold used to promote cards from the normal list into the priority queue.
- **Behavior**: If a normal card's sort value is below this number, it moves into the Priority Queue.
- **Multi-search**: Promoted cards form their own tier placed *after* all priority searches (so they are exempt from any single search's `limit=`). In `mix` mode the tier is folded into the single sorted pool, so promoted cards interleave with priority matches by sort value. For stricter placement, define an explicit `priority_search` instead.
- **Note**: If the top-level `sort_reverse` is `true`, cards with values *above* the threshold are moved instead.
- **Default**: `null`

### `tuning.priority_limit` (int | null)
- **Description**: A hard cap on the total number of cards allowed in the Priority Queue.
- **Behavior**: If the priority queue exceeds this count (after all other rules are applied), only the top N cards remain; the rest move to the Normal Queue.
- **Default**: `null`

### `tuning.shift_existing` (bool)
- **Description**: Whether to shift the position of existing new cards in your deck when repositioning. If `false`, cards are simply placed at the target positions, potentially overlapping.
- **Default**: `true`

---

## Sync Behavior (`sync_behavior`)

### `sync_behavior.reorder_on_sync` (bool)
- **Description**: When enabled, the addon will automatically run the reordering logic after each sync completes.
- **Alias**: the older top-level `reorder_after_sync` / `reorder_before_sync` spellings are still accepted, and are folded into this key automatically. So is the section's former name, `automation`.
- **Default**: `true`

### `sync_behavior.auto_update_dicts` (bool)
- **Description**: When enabled, the addon will automatically check your Jiten-sourced occurrence dictionaries and download any updates exactly once per day on sync.
- **Default**: `false`

---

## Matching (`matching`)

These flags all change how a card is credited with dictionary occurrences. They apply to
`occurrences:`/`seen:` wherever those run, in the reorder and the Browse bar alike.

### `matching.kana_normalization` (bool)
- **Description**: When enabled, katakana is folded to hiragana on both the card side and the dictionary index side before matching, so words that differ only by kana script are treated as equivalent.
- **Behavior**: Applied to both the expression and reading fields. Examples of pairs that match with this flag on:
    - Card `ギリギリ` / `ギリギリ` ↔ dict `ぎりぎり` / `ぎりぎり`
    - Card `南京` / `ナンキン` ↔ dict `南京` / `なんきん`
    - Card `南京錠` / `ナンキンじょう` ↔ dict `南京錠` / `なんきんじょう`
    - Card `ネタ帳` / `ネタちょう` ↔ dict `ねた帳` / `ねたちょう`
- **Default**: `false`

### `matching.combine_word_forms` (bool)
- **Description**: When enabled, occurrence lookups sum *all* readings stored under the card's expression plus any kana-only entries (㋕) attributed to the card's reading, instead of returning the count for the exact `(expression, reading)` pair only.
- **Behavior**: For a card with expression `南京` and reading `なんきん`, the count returned is the sum of every `南京` entry in the dictionary regardless of reading, plus every kana-only `なんきん` entry. Pure kana cards (where expression == reading) are not double-counted.
- **Note**: Independent of `matching.kana_normalization`, and both flags can be enabled together. Normalization is applied first, then the combined lookup runs against the normalized keys.
- **Default**: `false`

### `matching.variant_matching` (bool)
- **Description**: Credits a card with the counts of dict entries that are another **written form** of the same word, i.e. a different okurigana or kanji spelling. Prefix/suffix matching cannot reach these: `煌く` is neither a prefix nor a suffix of `煌めく`.
- **Rule**: an entry counts when its reading is *identical* to the card's **and** the two forms' kanji nest (every kanji of one appears in the other), with at least one kanji on each side. Requiring the kanji to nest rather than merely overlap keeps same-reading homophones apart, so `科学` is not credited by `化学`.
- **Kana**: kana-only entries have no kanji to share and never match here; enable `matching.combine_word_forms` too if you want those credited. Entries carrying no reading never match either.
- **Default**: `false`

### `matching.prefix_matching` (bool)
- **Description**: Also credits a card with the counts of longer dict entries that **start with** its expression (≥ 2 chars). Card `彫刻` (5) picks up `彫刻家` (100) + `彫刻品` (30) → 135.
- **Single kanji**: excluded from the bare rule; credited only via two reading-validated carve-outs.
  - *Particle entries*: `手を貸す`/てをかす and `俗に`/ぞくに credit `手`/て and `俗`/ぞく, but not `手`/しゅ (particles `を が の に で は も へ と`; anything after the particle is optional).
  - *Suru verbs*: an entry that is the kanji plus `する`/`じる`/`ずる` credits it when the reading matches: `屯する`/たむろする credits `屯`/たむろ (but not `屯`/とん), `感じる`/かんじる credits `感`/かん. The regular sound change before `する` is allowed, so `察する`/さっする credits `察`/さつ. Entries carrying no reading never match here, though they still count for the bare rule above.
- **Multi-character cards** need no carve-out: `勉強する` already starts with `勉強`, so the bare rule covers it.
- **Default**: `false`

### `matching.suffix_matching` (bool)
- **Description**: The mirror of `matching.prefix_matching` at the **end** of a word (Japanese is head-final). Groups a head with its family: `学校` ← `小学校`/`中学校`, `出す` ← `思い出す`, `強い` ← `心強い`.
- **Gate**: card must be **≥ 2 chars and contain a kanji** (real words like 学校/食べる/強い; excludes bare single kanji and pure kana like する/こと).
- **Single kanji**: excluded from the bare rule; credited only via reading-validated **tail** particle phrases, so `母の日`/ははのひ credits `日`/ひ.
- **Default**: `false`

### `matching.stem_matching` (bool)
- **Description**: Credits a dictionary-form card with the counts of its **conjugated noun form**: the 連用形 (masu-stem) for verbs, and the `さ`/`み`/`げ` nominalizations for い-adjectives. Card `戒める` picks up `戒め`, `遊ぶ` picks up `遊び`, `強い` picks up `強さ`/`強み`/`強げ`.
- **Rule**: the card's final kana is edited and the result must match a dict entry on **both** expression and reading. Ichidan verbs drop `る` (`戒める`→`戒め`), godan verbs shift う-row to い-row (`待つ`→`待ち`, `話す`→`話し`). The conjugation class is not looked up. Both candidates are tried and the reading decides, so the wrong one simply finds nothing.
- **Direction**: forward only. A `戒め` card is **not** credited by a `戒める` entry, because a rare derived form would inherit the count of a far commoner base word and jump the queue (`無げ` would absorb `無い`'s). Turn on `matching.prefix_matching` if you want that direction.
- **Gates**: the expression and reading must end in the *same* kana (that is what makes the tail okurigana), they must differ from each other (a kana-only card has nothing to validate against, so `それる` cannot absorb `それ`), and the derived form must be ≥ 2 chars (so `見る`→`見` and `神る`→`神` are both skipped).
- **Not covered**: `する` is irregular, so `勉強する` does not reach `勉強し`. `じる`/`ずる` verbs do work (`感じる`→`感じ`), since they inflect as ichidan.
- **Default**: `false`

### `matching.honorific_folding` (bool)
- **Description**: Credits a bare-form card with the counts of dict entries that start with an honorific (`お`/`ご`/`御`) and strip to the same word. Dict-side only, so a card `お茶` is unchanged. Card `茶` with `お茶` (50) + `茶` (10) → 60.
- **Gate**: the stripped remainder must contain a kanji (`お金`→`金`, `お茶の間`→`茶の間`) or itself be a dict entry. Kana-only strips need the entry, which blocks junk like `おかず`→`かず`.
- **Default**: `false`

---

## Search Syntax Cheat Sheet

The `occurrences:`, `f`, `kanji:`, `seen:`, and `length` terms below are **real Anki search terms**: besides
`priority_search`/`normal_search`, they work directly in the **Browse search bar** and through the
collection API (`col.find_cards` / `col.find_notes`, and therefore **AnkiConnect**). This lets you
test a priority search interactively in the browser before committing it to config. They honor the
same `matching` settings and the configured `word_fields` / `sort_field` as the reorderer.
Leading `-` negates a term as usual (e.g. `-occurrences:Dict>5`).

- **Anki Standard**: `added:3`, `deck:Japanese`, `tag:mining`, etc.
- **Frequency**: `f<=2000`. Matches cards where the sort field value is less than or equal to 2000. Useful for prioritizing common words across different search queries. Supports any comparison operator (`=`, `!=`, `<`, `<=`, `>`, `>=`).
- **Length**: `length>=3`. Matches cards whose expression field is 3 or more characters long (`length=1` for single-character words). Counts Unicode characters of the raw field value (markup included; an empty field is length 0). Supports any comparison operator.
- **Kanji i+1**: `kanji:new=1`. Matches words where exactly 1 character is unknown to you.
- **Kanji target**: `kanji:new[3]>=1`. A Kanji counts as "new" until 3 of your learned words contain it; matches words with at least 1 such Kanji. `kanji:new` is equivalent to `kanji:new[1]`.
- **New Reading**: `kanji:new_reading>=1`. Matches words where at least 1 Kanji is used in a *reading* no learned word has taught you. Once you know 食事 (しょくじ), 食べる (たべる) still matches, because 食=た is new. Takes the same bracket as `kanji:new`: `kanji:new_reading[3]>=1`. Requires `word_fields.expression_reading_field`.
- **Kanji Count**: `kanji:num=2`. Matches words containing exactly 2 Kanji.

- **Occurrences**: `occurrences:銀色、遥か>5`. Matches words appearing more than 5 times in the specified dictionary.
- **Multi-dict**: `occurrences:[Dict1,Dict2]>10`. Matches based on the combined count across multiple dictionaries.
- **All dicts**: `occurrences:all>5`. Combines the count across every dictionary in `user_files`.
- **Recently seen**: `seen:7`. Matches words appearing in any of the last 7 *daily* occurrence dictionaries (in `user_files/_seen/<YYYY-MM-DD>/`). It's boolean ("seen at all"). "Today" honors Anki's rollover hour. The `_seen` folder is reserved. It is never a normal occurrence dict, so `occurrences:_seen` and `occurrences:all` can't reach it, and only `seen:N` does. See the README for folder setup.
- **`limit=X`**: Use in a search string to take only the top X cards. **Config-only**: this is a reorder control, not a browser search term, and is ignored in the Browse bar.
  - Example: `added:3 limit=20` (Only the top 20 most frequent recent cards).

---

## Occurrence Setup (`word_fields`)

To use occurrences queries, you must configure which fields the addon should look at:

### `word_fields.expression_field` (string)
- **Description**: The field name containing the Japanese word or expression (e.g. `"Expression"`, `"Word"`).
- **Default**: `"Expression"`

### `word_fields.expression_reading_field` (string)
- **Description**: The field name containing the reading/furigana (e.g.  `"ExpressionReading"`,`"Reading"`).
- **Default**: `"ExpressionReading"`
- **Note**: Required by `occurrences:`, `seen:` and `kanji:new_reading`. If `kanji:new_reading` matches nearly every card, this is almost always the cause. The addon prints a warning to the console when it can't make sense of the readings it finds.

---

## Inspecting Results

After a reorder runs, **Tools** -> **Priority Reorder** -> **Show Summary** shows, per
`priority_search`, how many cards matched, were kept, and were discarded once these settings were
applied. It's the quickest way to tune `tuning.priority_cutoff`, `tuning.priority_limit`, and per-search
`limit=`, you can see the effect of each, open the kept/discarded notes in the Browser, and press
**Run reorder now** to re-check after editing the config. See the README's _Summary Window_ section
for more.