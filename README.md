# Priority Reorder Addon

Reorder your Anki cards to prioritize what matters most to you. 

## Overview
This addon ensures you learn the cards you think are most important first. Instead of seeing new cards in just frequency order, you can create a "Priority Queue" based on lots of different criteria:
- **Frequency**: Learn common words before rare ones (using frequency lists).
- **Immersion**: Prioritize words that appear in the VN/Book/Game/Show you are currently/planning on enjoying (using occurrence dictionaries).
- **Recency**: Learn cards you added recently rather than older cards.
- **Kanji**: Prioritize words based on your Kanji knowledge.
- **Content**: Prioritize specific decks, tags, or card types.

<details>
  <summary>View Example</summary>
  <br>
  <img src="example.png" alt="Example of priority reorder">

  <details>
    <summary>View My Config</summary>
    <br>
  
    ```
    {
      "normal_prioritization": null,
      "normal_search": "deck:日本語::Mining",
      "priority_cutoff": null,
      "priority_limit": null,
      "priority_search": [
        "deck:日本語::Mining occurrences:[9-nine-ここのつここのかここのいろ,9-nine-そらいろそらうたそらのおと,9-nine-はるいろはるこいはるのかぜ,9-nine-ゆきいろゆきはなゆきのあと]>=10",
        "deck:日本語::Mining occurrences:[9-nine-ここのつここのかここのいろ,9-nine-そらいろそらうたそらのおと,9-nine-はるいろはるこいはるのかぜ,9-nine-ゆきいろゆきはなゆきのあと]>=3",
        "deck:日本語::Mining occurrences:穢翼のユースティア>=3 added:14",
        "deck:日本語::Mining occurrences:穢翼のユースティア>=10 added:14",
        "deck:日本語::Mining occurrences:魔法少女ノ魔女裁判>=10 added:14",
        "deck:日本語::Mining occurrences:[うたわれるもの,うたわれるもの2,うたわれるもの3]>=20 added:14",
        "deck:日本語::Mining occurrences:穢翼のユースティア>=7",
        "deck:日本語::Mining occurrences:穢翼のユースティア>=5"
      ],
      "priority_search_mode": "sequential",
      "reorder_before_sync": true,
      "search_fields": {
        "expression_field": "Expression",
        "expression_reading_field": "ExpressionReading"
      },
      "shift_existing": true,
      "sort_field": "FreqSort",
      "sort_reverse": false
    }
    ```
  </details>

  As you can see, my current setup has several priority queues. I generally focus on my highest priorities being focused on frequent words from VN I'm currently reading. Later priority queues are more frequent cards in future VNs I want to read by the frequent cards.
</details>


## Installation
1. Install from [AnkiWeb](https://ankiweb.net/shared/info/857040600).
2. Restart Anki.

> This addon requires you to use a notetype with frequency data to function. I recommend [Lapis](https://github.com/donkuri/lapis) if you need one. If you need to backfill frequency data into existing cards, check out [backfill-anki-yomitan](https://github.com/Manhhao/backfill-anki-yomitan).

## Quick Start
By default, the addon ships with a default config that prioritizes cards added in the last 3 days, but you will need to customize it to your own deck and needs. To edit the config, follow these steps:

1. Go to **Tools** -> **Add-ons** -> **Priority Reorder** -> **Config**.
2. Edit your config with the fields you want to update (let's say you want to prioritize cards added in the last 5 days instead):
   ```json
   {
        "priority_search": [
            "deck:日本語::Mining added:5"
        ],
        "normal_search": "deck:日本語::Mining",
        "sort_field": "FreqSort"
   }
   ```
   > Note: If you have spaces in deck names or occurrence dictionary folder names, you will need to escape them like `"\"deck:日本語::Mining Deck\" added:5"`.
3. Change `"FreqSort"` to the actual name of the sort field in your note type (e.g., `"FreqSort"`, `"Frequency"`).
4. Press **OK**. 
5. The addon will automatically reorder your new cards **after** each sync completes. You can also press ``Ctrl+Alt+` `` to reorder manually.

> **Multi-device users**: Reordering runs *after* sync, so your desktop will always have fresh ordering. If you review on your phone, keep this in mind and either run a manual reorder (``Ctrl+Alt+` ``) before syncing or sync a second time to ensure your phone has the updated order.

## How it Works
The addon splits your **New Cards** into two groups:
1.  **Priority Queue**: Cards matching your `priority_search`. These will be shown *first*.
2.  **Normal Queue**: Cards matching your `normal_search`. These will be shown *after* the priority cards.

Both queues are sorted internally by your `sort_field`. If there are duplicates, those in the highest priority queue matching the card will take precedence. So in the example above, cards added in the last 3 days will be shown first and then even though those cards are also in the normal queue, the priority queue will have already scheduled them first.

## Features Guide
The addon supports several custom filters that you can mix in with standard Anki searches:
- **`f<10000`**: Filter by the value in your frequency sort field.
- **`occurrences:DictionaryName>5`**: Filter by word occurrences in a dictionary.
- **`limit=20`**: Limit the number of results from a specific search.
- **`kanji:num=1`**: Filter by the total number of Kanji.
- **`kanji:new=1`**: Filter by the number of unknown Kanji.

### 1. Frequency Sorting (`f`)
You can prioritize cards based on the numeric value in their sort field. This is most useful in combination with other filters, if you want to prioritize common words in an occurrence search for example.
- **Syntax**: `f<10000` or `f>=30000`. Supports all comparison operators: `=`, `!=`, `<`, `<=`, `>`, `>=`.

### 2. Occurrence Mining (`occurrences:`)
Prioritize words found in specific media (requires Yomitan dictionaries).
- **Syntax**: `occurrences:DictionaryName>=5` or `occurrences:[Dict1,Dict2]>=5`
- **Example**: `occurrences:銀色、遥か>=5` matches cards where the word appears 5 or more times in `銀色、遥か`.
- **Combined**: `occurrences:[銀色、遥か,穢翼のユースティア]>=10` matches cards where the combined frequency across both dictionaries is 10 or more.
- **All Dictionaries**: `occurrences:all>=10` is a special keyword that combines the occurrence counts from every dictionary in your `user_files` folder. Useful if you want to prioritize words that are common across all of your media.

#### Setup for Occurrence Dictionaries
> To use occurrence searching, you need Yomitan occurrence dictionaries. I highly recommend downloading them from [Jiten](https://jiten.moe/). They offer occurrence dictionaries for any media they have cataloged under `Download deck -> Yomitan (occurrences)` on each media page.

1. Go to **Tools** -> **Add-ons** -> **Priority Reorder** -> **View Files**.
2. Open the `user_files` folder.
3. Create a folder for your dictionary (e.g., `銀色、遥か`).
4. Inside that folder, place your `term_meta_bank_1.json` file (exported from [Jiten](https://jiten.moe/)), so that your folder structure looks like this:
   ```
   user_files/
   ├── 銀色、遥か/
   │   └── term_meta_bank_1.json
   └── 穢翼のユースティア/
       └── term_meta_bank_1.json
   ```
5. In your config, set `search_fields` to match your note type, which for [Lapis](https://github.com/donkuri/lapis) would be:
   ```json
   "search_fields": {
       "expression_field": "Expression",
       "expression_reading_field": "ExpressionReading"
   }
   ```

#### Prefix Matching
Set `"prefix_matching": true` in your config to allow a card to match with the counts of longer dictionary entries that start with the card's expression. This is useful when a short word shows up in the dictionary primarily as part of longer compounds.

- **Semantics**: `final_count = exact_count + Σ(counts of dict entries where card.expression is a proper written prefix)`.
- **Example**: With `彫刻` as the card expression in your deck, ocurrence dict entries `彫刻家` (100) and `彫刻品` (30) both start with `彫刻`, so `彫刻`'s effective count becomes `exact + 100 + 30`. A threshold like `occurrences:MyDict>=50` can now pick up `彫刻` even if it only appears as a standalone entry a handful of times.
- **Minimum length**: 2 characters. Single-character cards (e.g. `大`) are never credited via prefix matches, since the relationship is considered too loose to be meaningful.
- **Default**: `false`. Note that enabling this flag increases initial index startup time of the addon a bit, but not substantially.

#### Honorific Folding
Set `"honorific_folding": true` in your config to credit bare-form cards with the counts of dictionary entries that start with an honorific morpheme (`お`, `ご`, `御`) and whose stripped remainder is the same word. Useful when your dict counts `お茶` or `御社` separately from the bare form that's actually on the card.

- **Semantics**: a dict entry `お{X}` aliases its count onto `{X}`, but only when `{X}` is itself an entry in the same dict. This safety gate filters junk like `おはよう → はよう` and `御覧 → 覧`. Direction is dict-side only — a card for `お茶` is unchanged, but a card for `茶` picks up `お茶`'s count.
- **Example**: With dict entries `お茶` (50) and `茶` (10), a card for `茶` resolves to `10 + 50 = 60`. A card for `お茶` resolves to 50, unchanged.
- **Known limitation**: if the dict has `お金` but not bare `金`, a card for `金` is not credited — the gate refuses to alias onto a form the dict doesn't independently recognize. Combine with a supplementary dict via `occurrences:[A,B]` if you need to cover those cases.
- **Default**: `false`. Composes independently with `combine_word_forms`, `prefix_matching`, and `kana_normalization`.

#### Updating Occurrence Dictionaries
If your occurrence dictionaries were downloaded from [Jiten](https://jiten.moe/), the addon can keep them up to date automatically or on demand.

- **Manual Update**: Go to **Tools** -> **Priority Reorder** -> **Update Jiten Occurrence Dictionaries** to force-check all dictionaries for updates.
- **Auto Update**: Set `"auto_update_dicts": true` in your config to automatically attempt to update dictionaries once per day after syncing.

> **⚠️ Note for users with many dictionaries**: Jiten's API has a rate limit of roughly 10 requests per minute at the time of writing. If you have more than 10 occurrence dictionaries, updates will take extra time as the addon waits for the rate limit to reset. In this case, auto-updating on sync may not be recommended since it will delay your sync by over a minute. Use the manual update option instead if this bothers you.
   
### 3. Kanji Prioritization (`kanji:`)
Prioritize words based on your existing Kanji knowledge (scanned from your Review cards).
- **`kanji:new=0`**: Matches words where you *already know* all the characters.
- **`kanji:new=1`**: Matches words with exactly 1 unknown character.
- **`kanji:new>=2`**: Matches words with 2 or more unknown characters.
- **`kanji:num=1`**: Matches words with exactly 1 Kanji.
- **`kanji:num>=3`**: Matches words with 3 or more Kanji.

### 4. Multiple Priorities
Match multiple unrelated criteria by using a list.
- **Sequential**: First match `added:3`, THEN match `tag:ノベルゲーム::銀色、遥か`.
- **Mix**: Match `added:3` OR `tag:ノベルゲーム::銀色、遥か` and sort them all together.

### 5. Limits and Cutoffs
- **`limit=X`**: Use in a search string to take only the top X cards.
  - Example: `added:3 limit=20` (Only the top 20 most frequent recent cards).
- **`priority_limit`**: Global limit for the priority queue.
- **`priority_cutoff`**: Send high-frequency words back to the normal queue even if they matched priority.
