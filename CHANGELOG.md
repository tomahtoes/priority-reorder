# Changelog

## 2026-09-21
- Reorders are much faster. On a 22,700-note collection with 42 priority searches, a reorder went from about 15 seconds to under half a second, and the first one after starting Anki from about 18 seconds to about 3.
  - The biggest gain is for searches that put a custom term inside parentheses or next to `OR`, like `deck:X (seen:7 OR added:7) kanji:new>=1`. Those used to check every note in your collection instead of only the cards being reordered.
  - Collections large enough to reorder more than about 2,500 cards at once gain the most, since one of the database queries was reading the whole notes table every run.
  - Nothing about matching changed. The same cards are selected, in the same order.
- Every custom search term now answers to its first letter: `o:` for `occurrences:`, `k:` for `kanji:`, `s:` for `seen:`, and `l` for `length`. `occurrences:Dict>5 kanji:new=1 seen:7 length>=3` can be written `o:Dict>5 k:new=1 s:7 l>=3`.
  - Both spellings are the same term and can be mixed in one query. The long forms are unchanged, so existing configs keep working.
  - Works everywhere the long forms do: config searches, the Browse bar, and AnkiConnect.
  - `limit=` keeps its full name, because `l` belongs to `length`.
  - If one of your note types has a field named `o`, `k`, `s` or `l`, use the long form for that term, since the short form shadows a search on that field.
- New `compound_matching` occurrence option. Credits a dictionary-form card with the counts of entries *built on its stem*, which no other option can see.
  - `奮う` picks up `奮い立つ`, `取る` picks up `取り消す`, `受ける` picks up `受け入れる`, `食べる` picks up `食べ物`, `間違う` picks up `間違いない`.
  - Both the written form and the reading must match, so `抱く`/だく takes `抱きしめる` while `抱く`/いだく does not.
  - Works on its own (it covers the plain 連用形 too), and counts the shared entry once when `stem_matching` is also on.
  - Deliberately generous: transitive pairs (`見回る`←`見回す`) and drifted idioms (`当たる`←`当たり前`) come along with the real compounds.
  - Applies to `occurrences:` and `seen:` alike.

## 2026-08-28
- New `kanji:new_reading` search term. Prioritizes words that use a Kanji in a *reading* you haven't learned, not just a new Kanji, so `食べる` (た) still matches after you've learned `食事` (しょく).
  - Takes the same bracketed target as `kanji:new`. `kanji:new_reading[3]>=1` counts a reading as new until 3 learned words use it.
  - Inflections share a reading (`上がる`/`上げる`), and rendaku doesn't count as new (`血`/ち covers `鼻血`/はなぢ).
  - Jukujikun and gikun words like `火傷` (やけど) count as new for every Kanji they can't explain.
  - Requires `word_fields.expression_reading_field`.
- New `stem_matching` occurrence option. Credits a dictionary-form card with the counts of its *conjugated noun form*: the 連用形 (masu-stem) for verbs, and the `さ`/`み`/`げ` nominalizations for い-adjectives.
  - `戒める` picks up `戒め`, `遊ぶ` picks up `遊び`, `待つ` picks up `待ち`, and `強い` picks up `強さ`/`強み`.
  - No dictionary lookup is involved. Both the ichidan and godan candidates are tried and the reading decides, so `起きる` finds `起き` while `走る` finds `走り`.
  - Forward only. A `戒め` card is not credited by a `戒める` entry, since a rare derived form would otherwise inherit the count of a far commoner base word. Use `prefix_matching` if you want that.
  - Applies to `occurrences:` and `seen:` alike.
- `prefix_matching` now also credits single-kanji cards from their `する`/`じる`/`ずる` verb forms, so `屯`/たむろ gains `屯する` and `感`/かん gains `感じる`.
  - Reading-validated, so `屯`/とん, a different word, gains nothing.
  - The regular sound change before `する` still counts (`察`/さつ ← `察する`/さっする).
  - Applies to `occurrences:` and `seen:` alike.

## 2026-08-16
- Release [Daily Occurrences addon](https://github.com/tomahtoes/daily-occurrences) to allow tracking daily seen words.
- New `variant_matching` occurrence option. Credits a card with the counts of dictionary entries that are another *written form* of the same word, differing in okurigana or kanji spelling.
  - An entry counts when its reading matches the card's and the two forms' kanji nest, which keeps same-reading homophones apart.
  - Kana-only spellings never match here.
  - Applies to `occurrences:` and `seen:` alike.
- New `suffix_matching` occurrence option, the mirror of `prefix_matching` at the *end* of a word. A card is credited with the counts of dictionary entries that end with its expression, so a head morpheme aggregates its family.
  - `学校` ← `小学校`/`中学校`, and head-final compound verbs and adjectives like `出す` ← `思い出す` or `強い` ← `心強い`.
  - Restricted to real words: 2 or more characters, containing a kanji. A bare single kanji matches only via reading-validated particle phrases, where `母の日` credits `日`.
  - Applies to `occurrences:` and `seen:` alike.
- `kanji:new` now takes an optional per-kanji target. `kanji:new[3]>=1` matches words with at least one kanji that fewer than 3 of your learned words contain.
  - A kanji counts as "new" until the target number of learned words contain it.
  - Plain `kanji:new` is unchanged, and equivalent to `kanji:new[1]`.
- `prefix_matching` now also credits single-kanji cards from particle-linked entries when the entry's reading confirms the card's reading, so `手`/`て` gains `手を貸す`'s count.
  - Bare particle forms count too, so `俗`/`ぞく` gains `俗に` and `特`/`とく` gains `特に`. No separate card for the particle form is needed.
  - Applies to `occurrences:` and `seen:` alike.
- Summary window look-and-feel improvements: flatter, more compact layout with clearer rows.
- New `length` search filter. `length>=3` matches words 3 or more characters long, `length=1` single-character words.
- Performance enhancements, lower memory use, and assorted bug fixes.

## 2026-06-19
- New `seen:N` search term. Prioritizes words from your recent *daily* occurrence dictionaries (in `user_files/_seen/<YYYY-MM-DD>/`), matching words that appear in any of the last N days.
  - Works in the Browse bar, the collection API (AnkiConnect), and config.
  - Honors the occurrence options such as prefix matching.
- Assorted bug fixes: search `or` handling, summary window, custom-term caching, and an error during sync on close.
- Safer dictionary updating and lower memory use.
- Reorder-on-sync now skips repositioning when the new-card order is already correct, so syncs no longer stay stuck on "changes pending" after an unchanged reorder.
- A sync-triggered reorder now pushes the new card order in a single sync. Previously a second manual sync was needed before the new order took effect on other devices.

## 2026-06-04
- New **Summary window** (Tools → Priority Reorder → Show Summary).
- Jiten occurrence-dictionary updating, manual or automatic (`auto_update_dicts`).
- `prefix_matching` and `honorific_folding` occurrence options.
- `kana_normalization` option, treating katakana and hiragana variants as equivalent.
- `occurrences:all` shorthand to combine every dictionary in `user_files`.
- `occurrences:`, `f`, and `kanji:` now work in the Browse search bar and through the collection API (AnkiConnect), not just in config.
- Performance improvements.

## 2026-03-10
- Fixes to occurrence-entry handling.
- Fixed a `limit=` bug affecting later priority buckets.

## 2026-01-15
- Kanji prioritization (`kanji:new`, `kanji:num`).
- Multiple occurrence dictionaries with combined counts.
- Multiple priority queues (`sequential` / `mix` modes).

## 2025-09-11
- Occurrence-based prioritization (`occurrences:`).

## 2025-08-27
- Initial release: priority/normal queue reordering, frequency sorting via `sort_field`, reorder-on-sync, and a manual reorder hotkey.
