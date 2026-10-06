# Priority Reorder

New cards matching `priority_search` go first, then the rest of `normal_search`. Both groups sort by `sort_field`. Every search is limited to new cards.

Options under a section heading go inside that section:

    "matching": { "prefix_matching": true }

The [README](https://github.com/tomahtoes/priority-reorder#readme) has examples and the full matching rules.

## Top level

### priority_search
The search for the priority queue: one search, or a list of them. Default `""`.

### priority_search_mode
How a list of searches is ordered.

- `sequential` (default): each search's cards, one search after another.
- `mix`: all matches pooled and sorted together.
- `cycle`: the searches take turns, each placing its next `limit=` cards, until all run out. A search without `limit=` places everything on its first turn.

### normal_search
The search for the cards after the priority queue. Default `""`, meaning all new cards.

### sort_field
The numeric field both queues sort by, e.g. `FreqSort` (default) or `Frequency`. Cards with no number in it go last.

### sort_reverse
`false` (default) puts the lowest value first, `true` the highest.

## tuning

### priority_cutoff
Priority cards with a sort value above this go to the normal queue instead (below it with `sort_reverse`). Checked per search. Default `null`.

### normal_prioritization
Normal cards with a sort value below this join the priority queue (above it with `sort_reverse`). They go after every priority search and ignore `limit=`. In `mix` mode they sort in with the rest. Default `null`.

### priority_limit
The most cards the priority queue can hold, applied last. The rest go to the normal queue. Default `null`.

### shift_existing
`true` (default) moves other new cards back to make room. `false` writes positions from 0 up without moving anything else, so two cards can share a position.

## sync_behavior

### reorder_on_sync
Reorder after each sync, then sync again to upload the new order. Default `true`.

### auto_update_dicts
Check your Jiten occurrence dictionaries for updates once a day, on sync. Default `false`.

## today_new_limit

Raises a deck's new card limit for the day when the priority queue holds more cards than it allows. With a limit of 10 and 26 priority cards, you get 26 new cards that day. It sets the deck's Today only limit (the third tab in deck options), which Anki drops at the next day rollover. Your preset is never changed.

### enabled
Turns this on. Default `false`.

### decks
The deck you click to study, or a list of them. Default `[]`.

Name the deck you click, not the one the cards are in: Anki caps a study session at the clicked deck's limit. If you click `日本語` and the cards live in `日本語::Mining`, name `日本語`. A subdeck holding priority cards gets raised too when its own limit would hold them back.

### max
The highest the limit can go. Default `null`, no ceiling.

The new limit is the priority cards in the deck plus the new cards you've already studied today. It only goes up: if the queue fits under the deck's usual limit, nothing changes.

Each reorder recalculates it until you study your first new card in that deck. After that it stays put for the day, so cards you add in the afternoon don't keep raising it. A Today only limit you set yourself is left alone.

New cards also count toward the review limit unless the preset has "New cards ignore review limit" on. If you study on your phone before your computer has reordered that day, you get the usual limit until it does.

## matching

These change how `occurrences:` and `seen:` count a card. All default to `false`.

### kana_normalization
Treat katakana and hiragana as the same: `ギリギリ` matches `ぎりぎり`.

### combine_word_forms
Count every reading of the card's expression, plus kana-only entries of its reading, not just the exact expression and reading pair.

### prefix_matching
Add longer entries that start with the word: `彫刻` gets `彫刻家`. Words of 2+ characters only. A single kanji gets only phrases whose reading confirms it: `手を貸す` for `手`/て, `屯する` for `屯`/たむろ.

### suffix_matching
Add longer entries that end with the word: `学校` gets `小学校`, `出す` gets `思い出す`. Words of 2+ characters with a kanji only. Verbs also get entries ending in their negative or て-form (`にも拘わらず`, `急いで`). A single kanji gets only reading-confirmed phrases like `母の日`.

### variant_matching
Add other spellings with the same reading, where one spelling's kanji all appear in the other: `煌めく` gets `煌く`, `灯す` gets `燈す`. Kana-only spellings don't count.

### stem_matching
Add the noun form of a dictionary-form word: `戒める` gets `戒め`, `遊ぶ` gets `遊び`, `早い` gets `早く`, `強い` gets `強さ`. Never the reverse.

### compound_matching
Add compounds built on that stem, at either end: `取る` gets `取り消す`, `稼ぐ` gets `時間稼ぎ`. Also counts the stem itself. Deliberately loose.

### honorific_folding
Add `お`/`ご`/`御` forms to the bare word: `茶` gets `お茶`. Never the reverse.

## word_fields

### expression_field
The field holding the word. Default `Expression`.

### expression_reading_field
The field holding its kana reading. Default `ExpressionReading`. `occurrences:`, `seen:` and `kanji:new_reading` need it. If `kanji:new_reading` matches almost every card, this field is usually wrong; the console prints a warning.

## Search terms

These work in the searches above and in the Browse bar. Compare with `=` `!=` `<` `<=` `>` `>=`, and put `-` in front to negate.

- `f<=2000`: the sort field's value.
- `length>=3`: characters in the expression field, markup included.
- `occurrences:Name>5`: times the word appears in dictionary `Name`. `[A,B]` sums several, `all` sums every one.
- `seen:7`: appears in a daily dictionary from the last 7 days.
- `kanji:new=1`: number of kanji no learned word contains. `kanji:new[3]` keeps a kanji new until 3 do.
- `kanji:new_reading>=1`: number of kanji in a reading no learned word teaches. Takes `[3]` too.
- `kanji:num=2`: number of kanji.
- `limit=20`: keep this search's top 20. In `cycle` mode, cards per turn. Not a Browse bar term.

Short forms: `o:`, `k:`, `s:`, `l`. They work only while this addon is enabled, and a note type with a field named `o`, `k`, `s` or `l` needs the long form.

Dictionary folder names can't contain spaces.

**Tools → Priority Reorder → Show Summary** shows what each search kept and why the rest was dropped.
