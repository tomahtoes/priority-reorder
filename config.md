# Priority Reorder Config

## Core Settings

### `priority_search` (string | list)
- **Description**: The Anki search query used to identify cards for the Priority Queue. These cards will always be shown before the "Normal Queue". It can be a single string or a list of multiple search queries.
- **Support**: Supports standard Anki syntax plus custom filters like `kanji:new=1`, `kanji:num=2`, `f<10000`, or `occurrences:dict>5`.
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

### `sort_field` (string) — **Required**
- **Description**: The name of the field on your Note Type used for numeric sorting (e.g. `"FreqSort"`, `"Frequency"`).
- **Default**: `"FreqSort"`

### `sort_reverse` (bool)
- **Description**: Controls the sorting direction of the `sort_field`.
- **Behavior**: 
    - `false` (Ascending): Lowest values first
    - `true` (Descending): Highest values first
- **Default**: `false`

---

## Advanced Logic

### `priority_cutoff` (int | null)
- **Description**: A threshold used to bump cards from the priority queue.
- **Behavior**: If a priority card's sort value exceeds this number, it is moved to the Normal Queue.
- **Multi-search**: Applied to each priority bucket separately. Cards bumped from any bucket go to the normal list.
- **Note**: If `sort_reverse` is `true`, cards with values *below* the cutoff are moved instead.
- **Default**: `null`

### `normal_prioritization` (int | null)
- **Description**: A threshold used to promote cards from the normal list into the priority queue.
- **Behavior**: If a normal card's sort value is below this number, it moves into the Priority Queue.
- **Multi-search**: Promoted cards form their own tier placed *after* all priority searches (so they are exempt from any single search's `limit=`). In `mix` mode the tier is folded into the single sorted pool, so promoted cards interleave with priority matches by sort value. For stricter placement, define an explicit `priority_search` instead.
- **Note**: If `sort_reverse` is `true`, cards with values *above* the threshold are moved instead.
- **Default**: `null`

### `priority_limit` (int | null)
- **Description**: A hard cap on the total number of cards allowed in the Priority Queue.
- **Behavior**: If the priority queue exceeds this count (after all other rules are applied), only the top N cards remain; the rest move to the Normal Queue.
- **Default**: `null`

### `shift_existing` (bool)
- **Description**: Whether to shift the position of existing new cards in your deck when repositioning. If `false`, cards are simply placed at the target positions, potentially overlapping.
- **Default**: `true`

### `reorder_on_sync` (bool)
- **Description**: When enabled, the addon will automatically run the reordering logic after each sync completes.
- **Alias**: `reorder_after_sync` / `reorder_before_sync` are accepted as legacy fallbacks, but `reorder_on_sync` is the canonical key — prefer it.
- **Default**: `true`

### `auto_update_dicts` (bool)
- **Description**: When enabled, the addon will automatically check your Jiten-sourced occurrence dictionaries and download any updates exactly once per day on sync.
- **Default**: `false`

### `kana_normalization` (bool)
- **Description**: When enabled, katakana is folded to hiragana on both the card side and the dictionary index side before matching, so words that differ only by kana script are treated as equivalent.
- **Behavior**: Applied to both the expression and reading fields. Examples of pairs that match with this flag on:
    - Card `ギリギリ` / `ギリギリ` ↔ dict `ぎりぎり` / `ぎりぎり`
    - Card `南京` / `ナンキン` ↔ dict `南京` / `なんきん`
    - Card `南京錠` / `ナンキンじょう` ↔ dict `南京錠` / `なんきんじょう`
    - Card `ネタ帳` / `ネタちょう` ↔ dict `ねた帳` / `ねたちょう`
- **Default**: `false`

### `combine_word_forms` (bool)
- **Description**: When enabled, occurrence lookups sum *all* readings stored under the card's expression plus any kana-only entries (㋕) attributed to the card's reading, instead of returning the count for the exact `(expression, reading)` pair only.
- **Behavior**: For a card with expression `南京` and reading `なんきん`, the count returned is the sum of every `南京` entry in the dictionary regardless of reading, plus every kana-only `なんきん` entry. Pure kana cards (where expression == reading) are not double-counted.
- **Note**: Independent of `kana_normalization` — both flags can be enabled together. Normalization is applied first, then the combined lookup runs against the normalized keys.
- **Default**: `false`

### `prefix_matching` (bool)
- **Description**: When enabled, an occurrence lookup for a card's expression additionally credits the counts of every dictionary entry for which the expression is a **proper written prefix** (length ≥ 2).
- **Behavior**: Final count is `exact_count + Σ(counts of dict entries starting with card.expression)`. Example: a card `彫刻` (exact count 5) sees `彫刻家` (100) and `彫刻品` (30) as prefix matches and resolves to `5 + 100 + 30 = 135`. Single-character expressions are excluded (minimum length is 2, hardcoded). Stacks additively with `combine_word_forms`.
- **Default**: `false`

### `honorific_folding` (bool)
- **Description**: When enabled, dictionary entries that begin with an honorific morpheme (`お`, `ご`, `御`) also credit their count onto the bare form. Intended for cases where the stripped remainder is functionally the same word, e.g. `お茶` → `茶`, `お金` → `金`, `御社` → `社`.
- **Behavior**: Dict-side only — the alias adds to the *bare* form's lookup, not the other way around. A card `茶` with dict `お茶` (50) and `茶` (10) resolves to `10 + 50 = 60`. A card `お茶` resolves unchanged. The alias is only registered when the stripped remainder is itself an entry in the same dict; this filters non-words like `おはよう → はよう` or `御覧 → 覧` that would otherwise credit junk.
- **Known limitation**: if a dict contains `お金` but not bare `金`, a card for `金` will not be credited — the safety gate refuses to alias onto a form the dict doesn't independently recognize. Workaround: combine with a supplementary dict via `occurrences:[A,B]` that does index the bare form.
- **Note**: Independent of `kana_normalization`, `combine_word_forms`, and `prefix_matching`. All four flags compose additively.
- **Default**: `false`

---

## Search Syntax Cheat Sheet

The `occurrences:`, `f`, `kanji:`, and `seen:` terms below are **real Anki search terms**: besides
`priority_search`/`normal_search`, they work directly in the **Browse search bar** and through the
collection API (`col.find_cards` / `col.find_notes`, and therefore **AnkiConnect**). This lets you
test a priority search interactively in the browser before committing it to config. They honor the
same `kana_normalization` / `combine_word_forms` / `prefix_matching` / `honorific_folding` settings,
and the configured `search_fields` / `sort_field`, as the reorderer. Leading `-` negates a term as
usual (e.g. `-occurrences:Dict>5`).

- **Anki Standard**: `added:3`, `deck:Japanese`, `tag:mining`, etc.
- **Frequency**: `f<=2000` — Matches cards where the sort field value is less than or equal to 2000. Useful for prioritizing common words across different search queries. Supports any comparison operator (`=`, `!=`, `<`, `<=`, `>`, `>=`).
- **Kanji i+1**: `kanji:new=1` — Matches words where exactly 1 character is unknown to you.
- **Kanji Count**: `kanji:num=2` — Matches words containing exactly 2 Kanji.

- **Occurrences**: `occurrences:銀色、遥か>5` — Matches words appearing more than 5 times in the specified dictionary.
- **Multi-dict**: `occurrences:[Dict1,Dict2]>10` — Matches based on the combined count across multiple dictionaries.
- **All dicts**: `occurrences:all>5` — Combines the count across every dictionary in `user_files`.
- **Recently seen**: `seen:7` — Matches words appearing in any of the last 7 *daily* occurrence dictionaries (in `user_files/_seen/<YYYY-MM-DD>/`). Add an optional count test, `seen:7>=10` (default `>=1`). "Today" honors Anki's rollover hour. The `_seen` folder is reserved — it is never a normal occurrence dict, so `occurrences:_seen` / `occurrences:all` can't reach it; only `seen:N` does. See the README for folder setup.
- **`limit=X`**: Use in a search string to take only the top X cards. **Config-only** — this is a reorder control, not a browser search term, and is ignored in the Browse bar.
  - Example: `added:3 limit=20` (Only the top 20 most frequent recent cards).

---

## Occurrence Setup

To use occurrences queries, you must configure which fields the addon should look at:

### `search_fields.expression_field` (string)
- **Description**: The field name containing the Japanese word or expression (e.g. `"Expression"`, `"Word"`).
- **Default**: `"Expression"`

### `search_fields.expression_reading_field` (string)
- **Description**: The field name containing the reading/furigana (e.g.  `"ExpressionReading"`,`"Reading"`).
- **Default**: `"ExpressionReading"`

---

## Inspecting Results

After a reorder runs, **Tools** -> **Priority Reorder** -> **Show Stats** shows, per
`priority_search`, how many cards matched, were kept, and were discarded once these settings were
applied. It's the quickest way to tune `priority_cutoff`, `priority_limit`, and per-search
`limit=` — you can see the effect of each, open the kept/discarded notes in the Browser, and press
**Run reorder now** to re-check after editing the config. See the README's _Stats Window_ section
for more.