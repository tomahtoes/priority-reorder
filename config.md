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
- **Multi-search**: Promoted cards are appended to the *last* priority bucket. For stricter placement, define an explicit `priority_search` instead.
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
- **Default**: `true`

---

## Search Syntax Cheat Sheet

- **Anki Standard**: `added:3`, `deck:Japanese`, `tag:mining`, etc.
- **Frequency**: `f<=2000` — Matches cards where the sort field value is less than or equal to 2000. Useful for prioritizing common words across different search queries. Supports any comparison operator (`=`, `!=`, `<`, `<=`, `>`, `>=`).
- **Kanji i+1**: `kanji:new=1` — Matches words where exactly 1 character is unknown to you.
- **Kanji Count**: `kanji:num=2` — Matches words containing exactly 2 Kanji.

- **Occurrences**: `occurrences:銀色、遥か>5` — Matches words appearing more than 5 times in the specified dictionary.
- **Multi-dict**: `occurrences:[Dict1,Dict2]>10` — Matches based on the combined count across multiple dictionaries.
- **`limit=X`**: Use in a search string to take only the top X cards.
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