# Priority Reorder Addon

Reorder your new Anki cards so the ones you care about come first.

## Changelog
See [CHANGELOG.md](CHANGELOG.md).

## Overview
Instead of learning new cards in plain frequency order, you build a priority queue from searches like these:

- common words first, using your frequency field
- words from the VN, book, game or show you're reading now or plan to, using occurrence dictionaries
- cards you added recently
- words whose kanji, or kanji readings, you don't know yet
- specific decks, tags or note types

<details>
  <summary>View Example</summary>
  <br>
  <img src="example.png" alt="Example of priority reorder">

  <details>
    <summary>View Example Config</summary>
    <br>

    ```
    {
      "normal_search": "deck:日本語::Mining",
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
      "sort_field": "FreqSort",
      "sort_reverse": false,
      "tuning": {
        "priority_cutoff": null,
        "normal_prioritization": null,
        "priority_limit": null,
        "shift_existing": true
      },
      "sync_behavior": {
        "reorder_on_sync": true,
        "auto_update_dicts": false
      },
      "word_fields": {
        "expression_field": "Expression",
        "expression_reading_field": "ExpressionReading"
      }
    }
    ```
  </details>

  My first searches pick the most frequent words in the VN I'm reading now. Later searches pick frequent words from VNs I plan to read next.
</details>


## Installation
1. Install from [AnkiWeb](https://ankiweb.net/shared/info/857040600).
2. Restart Anki.

> You need a note type with frequency data. I recommend [Lapis](https://github.com/donkuri/lapis). To add frequency data to existing cards, see [backfill-anki-yomitan](https://github.com/Manhhao/backfill-anki-yomitan).

## Quick Start
The default config prioritizes cards added in the last 3 days. To change it:

1. Go to **Tools** → **Add-ons**, select **Priority Reorder** and press **Config**.
2. Edit the settings you want. For cards added in the last 5 days:
   ```json
   {
        "priority_search": [
            "deck:日本語::Mining added:5"
        ],
        "normal_search": "deck:日本語::Mining",
        "sort_field": "FreqSort"
   }
   ```
   > A deck name with spaces needs escaped quotes: `"\"deck:日本語::Mining Deck\" added:5"`. Occurrence dictionary folder names can't contain spaces at all, so rename the folder.
3. Set `sort_field` to the frequency field of your note type (e.g. `"FreqSort"` or `"Frequency"`).
4. Press **OK**.

The addon reorders your new cards after every sync, then syncs once more so your other devices get the new order. Press ``Ctrl+Alt+` `` to reorder by hand.

The searches and sorting sit at the top level of the config. Everything else is grouped into the `matching`, `tuning`, `sync_behavior`, `today_new_limit` and `word_fields` sections.

## How it Works
The addon splits your new cards into two queues:

1. **Priority queue**: cards matching `priority_search`, shown first.
2. **Normal queue**: cards matching `normal_search`, shown after.

Each queue is sorted by `sort_field`. A card matching both goes in the priority queue, so in the example above, recent cards are placed first even though they also match the normal search.

Everything lives under **Tools** → **Priority Reorder**: **Reorder Cards** (``Ctrl+Alt+` ``), **Show Summary**, and **Update Jiten Occurrence Dictionaries**.

## Summary Window
**Tools** → **Priority Reorder** → **Show Summary** shows what each priority search did in the latest reorder this session. If nothing has run yet, press **Run reorder** or sync.

The top shows how many cards were prioritized, when, and a bar splitting the priority queue by search. Below, each search gets a row with its **kept** and **matched** counts, its place in the queue, and a thin bar showing where its matches went: kept, taken by an earlier search, over its `limit=`, or cut by `priority_cutoff`. Click a row for the numbers and for buttons that open those notes in the Browser. **Edit config** and **Run reorder** are at the top, so you can change a setting and check the result straight away.

In `"mix"` mode the searches are pooled before sorting, so each row shows only its **matched** count.

In `"cycle"` mode, a search that takes several turns shows two ranges, such as `1–5, 2981–3400 ↻`: its first turn, then the stretch where its later turns alternate with the other searches. Hover the range for the number of turns. The bar shows each first turn in place, then a striped stretch for the later turns; hover it to see which searches placed how many cards there.

## Search Terms
Mix these into any Anki search:

| Term | Short | Matches by |
|---|---|---|
| `f<10000` | | value of your sort field |
| `length>=3` | `l>=3` | characters in the expression field |
| `occurrences:Name>5` | `o:Name>5` | times the word appears in an occurrence dictionary |
| `seen:7` | `s:7` | whether the word appeared in the last 7 days |
| `kanji:new=1` | `k:new=1` | number of kanji you haven't learned |
| `kanji:new_reading>=1` | `k:new_reading>=1` | number of kanji in a reading you haven't learned |
| `kanji:num=2` | `k:num=2` | number of kanji |
| `limit=20` | | keeps only the top 20 of this search (config only) |

All of them except `limit=` also work in the Browse search bar and through the collection API (and so AnkiConnect), which makes the Browser a good place to try a search before you put it in your config. They use the same `matching` settings, `word_fields` and `sort_field` as the reorder. Comparisons can be `=`, `!=`, `<`, `<=`, `>` or `>=`, and a leading `-` negates a term (`-seen:30`).

The short forms are the same terms, and you can mix the two. `limit=` has no short form, because `l` belongs to `length`. If one of your note types has a field named `o`, `k`, `s` or `l`, the short form hides searches on that field, so use the long form there. Short forms also work only while this addon is enabled, so write the long form in a search you plan to share.

### Frequency (`f`)
`f<10000` or `f>=30000` compares the number in your sort field. It's most useful combined with other terms, e.g. to keep only the common words from an occurrence search.

### Length (`length`)
`length>=3` matches expressions of 3 or more characters, `length=1` single-character words. The raw field value is counted in Unicode characters, with no HTML stripping, so markup counts toward the length. An empty field has length 0.

### Occurrences (`occurrences:`)
Prioritize words from specific media, using Yomitan occurrence dictionaries.

- `occurrences:銀色、遥か>=5` matches words that appear 5 or more times in `銀色、遥か`.
- `occurrences:[銀色、遥か,穢翼のユースティア]>=10` adds up the counts from both.
- `occurrences:all>=10` adds up every dictionary in `user_files`, for words common across all your media.

#### Setting up occurrence dictionaries
Download them from [Jiten](https://jiten.moe/): each media page has them under `Download deck -> Yomitan (occurrences)`.

1. Go to **Tools** → **Add-ons**, select **Priority Reorder** and press **View Files**.
2. Open the `user_files` folder.
3. Create a folder named after the dictionary, e.g. `銀色、遥か`. The folder name is what you type after `occurrences:`, so it can't contain spaces.
4. Unzip the Jiten download into it. The addon reads every `term_meta_bank_*.json`, and the updater needs `index.json`:
   ```
   user_files/
   ├── 銀色、遥か/
   │   ├── index.json
   │   └── term_meta_bank_1.json
   └── 穢翼のユースティア/
       ├── index.json
       └── term_meta_bank_1.json
   ```
5. Set `word_fields` to match your note type. For [Lapis](https://github.com/donkuri/lapis):
   ```json
   "word_fields": {
       "expression_field": "Expression",
       "expression_reading_field": "ExpressionReading"
   }
   ```

#### Updating occurrence dictionaries
The addon can update dictionaries that came from Jiten.

- **Tools** → **Priority Reorder** → **Update Jiten Occurrence Dictionaries** checks all of them now.
- `"auto_update_dicts": true` in the `sync_behavior` section checks once a day, after a sync.

> Jiten's API allows roughly 10 requests per minute, so with more than 10 dictionaries an update pauses to wait out the limit. If that makes syncing feel slow, leave `auto_update_dicts` off and update from the menu.

### Matching options
By default a card counts only the entries that exactly match its expression and reading. The options in the `matching` section also credit it with related entries:

```json
"matching": { "prefix_matching": true, "variant_matching": true }
```

They apply wherever `occurrences:` and `seen:` run, in the reorder and in the Browse bar. All are off by default, and they combine without counting an entry twice.

| Option | Card gets the counts of | Example |
|---|---|---|
| `kana_normalization` | the same word in the other kana script | `ギリギリ` ← `ぎりぎり` |
| `combine_word_forms` | every reading of its expression, plus kana-only entries of its reading | `南京` ← all `南京` entries |
| `prefix_matching` | longer entries that start with it | `彫刻` ← `彫刻家` |
| `suffix_matching` | longer entries that end with it | `学校` ← `小学校` |
| `variant_matching` | other spellings of the same word | `煌めく` ← `煌く` |
| `stem_matching` | its noun form | `戒める` ← `戒め` |
| `compound_matching` | compounds built on its stem | `取る` ← `取り消す` |
| `honorific_folding` | its `お`/`ご`/`御` form | `茶` ← `お茶` |

The full rules follow. Every option only adds counts, so the worst a loose match can do is push a card over a threshold early.

<details>
<summary><code>kana_normalization</code> and <code>combine_word_forms</code></summary>

`kana_normalization` folds katakana to hiragana on both the card and the dictionary before matching, in the expression and the reading. These pairs match with it on:

- `ギリギリ`/`ギリギリ` and `ぎりぎり`/`ぎりぎり`
- `南京`/`ナンキン` and `南京`/`なんきん`
- `ネタ帳`/`ネタちょう` and `ねた帳`/`ねたちょう`

`combine_word_forms` sums every entry under the card's expression, whatever its reading, plus every kana-only entry (㋕) for the card's reading. A `南京`/`なんきん` card gets all `南京` entries and all kana-only `なんきん` entries. A kana card, whose expression is its reading, isn't counted twice.

The two are independent. With both on, the kana is folded first and the combined lookup runs on the folded keys.
</details>

<details>
<summary><code>prefix_matching</code></summary>

The card gets the counts of every longer entry that starts with its expression. With entries `彫刻家` (100) and `彫刻品` (30), a `彫刻` card counts its own entry plus 130, so `occurrences:MyDict>=50` can pick it up even if `彫刻` alone appears only a few times.

- **2 characters minimum.** A single character is too loose: `手` would absorb `手紙` and `手術`, where it is only part of the word and often read differently.
- **Single kanji, particle phrases.** A single-kanji card with a reading is credited by entries of the form kanji + particle (`を が の に で は も へ と`), optionally followed by more, when the entry's reading starts with the card's reading plus that particle. `手を貸す` (てをかす) credits `手`/て but not `手`/しゅ, and `手紙` still gives nothing. The bare form counts too: `俗に` credits `俗`/ぞく and `特に` credits `特`/とく, so you don't need separate cards for them. The reading check is what keeps out on'yomi compounds and verbs like `積もる`.
- **Single kanji, する verbs.** An entry that is the kanji plus `する`, `じる` or `ずる` credits it when the reading matches: `屯する`/たむろする credits `屯`/たむろ but not `屯`/とん, and `感じる` credits `感`/かん. The sound change before `する` is allowed, so `察する`/さっする credits `察`/さつ.
- Entries with no reading never count for the single-kanji rules.
- A multi-character card needs none of this: `勉強する` already starts with `勉強`.
- Turning it on adds a little to the addon's startup time.
</details>

<details>
<summary><code>suffix_matching</code></summary>

The mirror of prefix matching: the card gets the counts of longer entries that end with its expression. Japanese compounds put the head last, so this gathers a word's family: `学校` gets `小学校`, `中学校` and `高等学校`.

- **2+ characters with a kanji.** That covers kanji compounds (`学校`, `目的`) and kanji-plus-okurigana words (`食べる`, `強い`), so compound verbs and adjectives work: `出す` gets `思い出す` and `飛び出す`, `強い` gets `心強い` and `力強い`. A bare single kanji (`語`, `日`, `手`) would absorb whole families with unstable readings and meanings, so it is left out, and so are pure kana cards (`する`, `こと`, loanwords).
- **Single kanji, particle phrases.** As with prefixes, a single kanji is credited only by entries ending in a particle plus the kanji, when the reading ends in that particle plus the card's reading. `母の日` (ははのひ) credits `日`/ひ, not `日`/にち, and `今日` or `日本語` give nothing.
- **Negative forms.** A verb is credited by entries ending in its negative stem plus `ず` or `ぬ`: `にも拘わらず` credits `拘わる`, `相変わらず` credits `変わる`, `見ず知らず` and `見知らぬ` credit `知る`, and `思わず` credits `思う`. The reading has to end the same way, so `にも拘らず` (にもかかわらず) doesn't credit `拘る`/こだわる and `水入らず` (みずいらず) doesn't credit `入る`/はいる. `ない` is left out, because `つまらない` and `くだらない` would inflate their base verbs.
- **て-forms.** Likewise for entries ending in the verb's て-form, which dictionaries list for adverbs and set phrases: `急いで` credits `急ぐ`, `に沿って` credits `沿う`, `この期に及んで` credits `及ぶ`, `謹んで` credits `謹む`. The reading has to match here too.
- Turning it on adds a little to the addon's startup time.
</details>

<details>
<summary><code>variant_matching</code></summary>

The card gets the counts of other spellings of the same word, differing in okurigana or kanji. Prefix and suffix matching can't reach these: `煌く` is neither a prefix nor a suffix of `煌めく`.

- **Rule.** An entry counts when its reading is identical to the card's and the kanji of one form all appear in the other, with at least one kanji on each side. A `煌めく` card gets `煌く` but not `燦めく` (no shared kanji) or `きらめく` (no kanji). Okurigana families fold together: `落葉`/`落ち葉`, `気持`/`気持ち`, `子供`/`子ども`.
- **Why all the kanji, not one.** Homophones often share a kanji but are different words. Requiring one form's kanji to sit inside the other keeps `科学` and `化学`, `保証` and `保障`, `対象` and `対照` apart.
- **Glyph variants.** Kanji that KANJIDIC2 lists as variants of each other count as one kanji: `燈す`/`灯す`, `掻く`/`搔く`, `醤油`/`醬油`, `怒涛`/`怒濤`, `籠城`/`篭城`.
- **Kana spellings don't count**, since they have no kanji to compare. Turn on `combine_word_forms` as well if you want them. Entries with no reading never match.
- Free while off. When on, each dictionary builds an index once, on first use.
</details>

<details>
<summary><code>stem_matching</code></summary>

The card, in dictionary form, gets the counts of its noun form: the 連用形 (masu-stem) for verbs, and for い-adjectives the `く` form and the `さ`/`み`/`げ` nouns. Occurrence dictionaries list these as separate entries, so without this a `戒める` card scores nothing against a dictionary that has only `戒め`.

- **Rule.** The card's last kana is changed, and the result has to match an entry in both expression and reading. Ichidan verbs drop `る` (`戒める`→`戒め`). Godan verbs move the last kana from the う-row to the い-row (`遊ぶ`→`遊び`, `待つ`→`待ち`, `話す`→`話し`, `泳ぐ`→`泳ぎ`). い-adjectives take `く` (`早い`→`早く`) and all three nouns (`強い`→`強さ`/`強み`/`強げ`).
- **No verb-class lookup.** Both the ichidan and the godan form are tried and the reading decides, so `起きる` finds `起き`, `走る` finds `走り`, and the wrong guess matches nothing.
- **One direction only.** A `戒め` card is not credited by `戒める`. The other way round, a rare derived form would take the count of a far commoner base word and jump the queue (`無げ`, seen once, would absorb the thousands of `無い`). Use `prefix_matching` if you want that direction.
- **Gates.** The expression and reading must end in the same kana, which is what makes the tail okurigana, so kanji-final words like `学校` never qualify. They must also differ, so a kana-only card can't validate a match (`それる` won't absorb `それ`). The noun form must be at least 2 characters, which skips `見る`→`見` and `神る`→`神`.
- **Not covered.** `する` is irregular, so `勉強する` doesn't reach `勉強し`. `じる` and `ずる` verbs do work (`感じる`→`感じ`), because they conjugate as ichidan.
- **Known misses.** A card ending in `る` that is really a past form gets caught (`来たる`←`来た`), and a 連用形 noun ending in `い` is treated as an adjective (`囲い`←`囲み`). Across 13 dictionaries that was 2 of 917 matches.
- Costs nothing while off, and nothing extra while on: it builds no index and adds no startup time or memory.
</details>

<details>
<summary><code>compound_matching</code></summary>

The card, in dictionary form, gets the counts of entries built on its stem. Much verb vocabulary lives here: a dictionary listing `奮い立つ` tells no other option anything about `奮う`, because it neither starts nor ends with `奮う` and doesn't share its reading.

- **Rule.** The same stems stem matching makes, used as a prefix instead of an exact match. `奮う` gets `奮い立つ`, `取る` gets `取り消す` and `取り扱い`, `受ける` gets `受け入れる`, `食べる` gets `食べ物`, `間違う` gets `間違いない` and `間違いなく`, `少ない` gets `少なくとも`.
- **Both sides must match.** The entry has to start with the stem in writing and in reading, which is why `抱く`/だく gets `抱きしめる`/だきしめる and `抱く`/いだく doesn't.
- **Compounds ending in the stem.** The stem also counts as the last part of a compound: `稼ぐ` gets `時間稼ぎ`, `止まる` gets `行き止まり`, `惑う` gets `戸惑い`, `休む` gets `夏休み`. The reading may voice the stem's first sound, as compounds usually do (`手触り`, てざわり, credits `触る`).
- **Works alone.** It covers the bare stem too. With `stem_matching` also on, that entry is counted once.
- **No overlap with prefixes.** Entries that start with the card as written (`食べる`←`食べるもの`) belong to `prefix_matching` whether or not that is on.
- **How loose.** Across 12 dictionaries, 942 of 9,877 eligible entries gained something, by a median of 9. The gains are real compounds, but the rule can't tell a compound from a relative: transitive pairs cross over (`見回る`←`見回す`, `起こる`←`起こす`), idioms ride along (`当たる`←`当たり前`), and a rare base can take a common word's count (`生く`/いく, seen twice, absorbs the compounds of `生きる`). Kana-only cards are excluded as in stem matching, so loanwords are safe.
- Free while off. When on, each dictionary sorts its entries once, on first use.
</details>

<details>
<summary><code>honorific_folding</code></summary>

The card gets the counts of entries that are the same word with `お`, `ご` or `御` in front, which is useful when a dictionary lists `お茶` or `御社` and your card is the bare form.

- **Rule.** An entry `お{X}` adds its count to `{X}` when `{X}` contains a kanji (`お茶の間`→`茶の間`, `お金`→`金`) or is itself an entry in the same dictionary. A kana-only remainder needs that entry, which blocks junk like `おかず`→`かず` and `おはよう`→`はよう`.
- **One direction.** An `お茶` card is unchanged, a `茶` card gains `お茶`'s count. With entries `お茶` (50) and `茶` (10), `茶` counts 60 and `お茶` 50. With only `お茶の間` (9), a `茶の間` card counts 9.
- **Known misses.** A remainder with a kanji is folded even when it's a different word or reading: a `飯`/めし card picks up `ご飯`/ごはん.
</details>

### Kanji (`kanji:`)
Prioritize words by the kanji you already know. A kanji is known once it appears in the expression of a card you've learned (a review or relearning card that isn't suspended). Each example also works with `k:`.

- `kanji:new=0`: every kanji is known.
- `kanji:new=1`: exactly 1 unknown kanji.
- `kanji:new>=2`: 2 or more unknown kanji.
- `kanji:num=1`: exactly 1 kanji.
- `kanji:num>=3`: 3 or more kanji.

A kanji stops being new as soon as one learned word contains it. To raise the bar, add a target: with `kanji:new[3]`, a kanji stays new until 3 learned words contain it (`kanji:new` is `kanji:new[1]`). This helps keep practising kanji you've met in only one or two words. The target works on `new` and `new_reading`, not on `num`.

#### New readings (`kanji:new_reading`)
`kanji:new` asks whether you've met the kanji. `kanji:new_reading` asks whether you've met the reading it has in this word, which is what the card tests.

Once you've learned 食事 (**しょく**じ), `kanji:new` counts 食べる (**た**べる) as fully known, though 食=た is new to you. That's the card you're about to fail.

- `kanji:new_reading>=1`: at least 1 kanji in a reading you haven't learned.
- `kanji:new_reading>=1 kanji:new=0`: words made entirely of kanji you know, in a reading you don't.
- `kanji:new_reading[3]>=1`: a reading stays new until 3 learned words use it.

Readings are tracked per kanji, so 生活 (**せい**かつ) doesn't teach 生きる (**い**きる). Inflections share a reading: 上がる and 上げる are both 上=あ, while 上る (のぼる) is different. Rendaku isn't a new reading either: 血 (**ち**) covers 鼻血 (はな**ぢ**).

Words whose reading doesn't split across their kanji, like 火傷 (やけど), 今日 (きょう) and gikun readings, count as new for every kanji they can't explain. Those are the least predictable readings, so that's usually what you want. When only part of a word is irregular, only that part counts: 眼鏡/めがね credits 眼=め and flags only 鏡.

> Set `word_fields.expression_reading_field` to the field with the kana reading. Cards without a reading never match. Readings are checked against a bundled table built from [KANJIDIC2](https://www.edrdg.org/wiki/index.php/KANJIDIC_Project). If this term matches almost every card, the reading field is usually the cause, and the console prints a warning.

Example: `occurrences:銀色、遥か>=5 kanji:new=0 kanji:new_reading>=1` finds words common in the VN you're reading that look fully known but will trip you up.

### Recently seen words (`seen:`)
> Experimental: `seen:` may change or be removed in a future version.

Prioritize words from your recent immersion, using *daily* occurrence dictionaries. The `matching` options apply to `seen:` too.

- `seen:N` (short `s:N`) matches words in the daily dictionary of any of the last **N** days, however many times they appear. `-seen:30` negates it.
- `seen:1` is today. "Today" follows Anki's "Next day starts at" setting.
- **Keep the window small.** Cost grows with the number of days: `seen:1` to `seen:3` are cheap, while `seen:30` and up get noticeably slower, more so with `prefix_matching` or `variant_matching` on. Use the smallest window that still means "recent". Reusing the same window in several searches costs nothing extra.

#### Setting up seen dictionaries
My [Daily Occurrences addon](https://github.com/tomahtoes/daily-occurrences) writes one for each day, from text that arrives over a websocket (the usual VN setup).

To build them yourself, put them in a `_seen` folder under `user_files`, one subfolder per day named `YYYY-MM-DD`:
```
user_files/
└── _seen/
    ├── 2026-06-11/
    │   └── term_meta_bank_1.json
    └── 2026-06-12/
        └── term_meta_bank_1.json
```
Each `term_meta_bank_*.json` is an ordinary Yomitan occurrence dictionary and uses the same `word_fields`. The `_seen` folder is reserved: it isn't an occurrence dictionary, so `occurrences:all` skips it and `occurrences:_seen` can't reach it. Only `seen:N` reads it.

### Multiple searches
Make `priority_search` a list, and `priority_search_mode` decides how the searches combine:

- `"sequential"`: all of the first search, then the second, and so on. `["added:3", "tag:ノベルゲーム::銀色、遥か"]` puts recent cards first, then the 銀色、遥か cards.
- `"mix"`: every match in one pool, sorted together.
- `"cycle"`: the searches take turns, each placing its next `limit=` cards, until all run out. `["deck:A limit=10", "deck:B", "deck:C limit=5"]` places A's top 10, all of B, C's top 5, then A's next 10, C's next 5, and so on. Without any `limit=` this is the same as `"sequential"`.

### Limits and cutoffs
- `limit=X` in a search keeps only its top X cards (`added:3 limit=20`: the 20 most frequent recent cards). In `"cycle"` mode it's the number of cards per turn, and the rest wait for the next turn.
- `tuning.priority_limit` caps the whole priority queue.
- `tuning.priority_cutoff` sends priority cards whose sort value is past this number (the rarer words, with a frequency-rank field) to the normal queue.
- `tuning.normal_prioritization` does the opposite: normal cards with a sort value under this number join the priority queue, after all your searches.

### Fitting the day's new cards to the queue
With `today_new_limit` on, a deck's new card limit rises for the day to fit the priority queue: 26 priority cards on a 10-a-day preset gives you 26 that day. It uses Anki's Today only limit, so the preset is never changed.

```json
"today_new_limit": { "enabled": true, "decks": ["日本語"], "max": 60 }
```

Name the deck you click to study. The limit is recalculated on each reorder until you study your first new card of the day, then holds. [config.md](config.md#today_new_limit) has the details.

## Credits
Kanji reading and variant data is derived from **KANJIDIC2**, Copyright © the
[Electronic Dictionary Research and Development Group](https://www.edrdg.org/wiki/index.php/KANJIDIC_Project),
used under the [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) licence. The bundled
`kanji_readings.txt` and `kanji_variants.txt` are modified extracts (readings and variant groups only, re-encoded) and are likewise CC BY-SA 4.0.
