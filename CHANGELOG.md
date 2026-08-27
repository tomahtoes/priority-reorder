# Changelog

## 2026-08-27
- New `kanji:new_reading` search term — prioritize words that use a Kanji in a *reading* you haven't learned, not just a new Kanji: after `食事` (しょく), `食べる` (た) still matches. Takes the same bracketed target as `kanji:new` (`kanji:new_reading[3]>=1` counts a reading as new until 3 learned words use it). Inflections share a reading (`上がる`/`上げる`) and rendaku doesn't count as new (`血`/ち covers `鼻血`/はなぢ), while jukujikun and gikun words like `火傷` (やけど) count as new for every Kanji they can't explain. Requires `search_fields.expression_reading_field`.
- `prefix_matching` now also credits single-kanji cards from their `する`/`じる`/`ずる` verb forms: `屯`/たむろ gains `屯する`, `感`/かん gains `感じる`. Reading-validated, so `屯`/とん (a different word) gains nothing, while the regular sound change before `する` still counts (`察`/さつ ← `察する`/さっする). Applies to `occurrences:` and `seen:` alike.

## 2026-08-16
- Release [Daily Occurrences addon](https://github.com/tomahtoes/daily-occurrences) to allow tracking daily seen words.
- New `variant_matching` occurrence option — credits a card with the counts of dictionary entries that are another *written form* of the same word, differing in okurigana or kanji spelling. An entry counts when its reading matches the card's and the two forms' kanji nest, which keeps same-reading homophones apart; kana-only spellings never match here. Applies to `occurrences:` and `seen:` alike.
- New `suffix_matching` occurrence option — the mirror of `prefix_matching` at the *end* of a word. A card is credited with the counts of dictionary entries that end with its expression, so a head morpheme aggregates its family: `学校` ← `小学校`/`中学校`, and head-final compound verbs/adjectives like `出す` ← `思い出す` or `強い` ← `心強い`. Restricted to real words (≥ 2 characters with a kanji); a bare single kanji matches only via reading-validated particle phrases (`母の日` credits `日`). Applies to `occurrences:` and `seen:` alike.
- `kanji:new` now takes an optional per-kanji target: `kanji:new[3]>=1` matches words with at least one kanji that fewer than 3 of your learned words contain. A kanji counts as "new" until the target number of learned words contain it; plain `kanji:new` is unchanged (equivalent to `kanji:new[1]`).
- `prefix_matching` now also credits single-kanji cards from particle-linked entries (e.g. `手`/`て` gains `手を貸す`'s count) when the entry's reading confirms the card's reading. Bare particle forms count too, so `俗`/`ぞく` gains `俗に` and `特`/`とく` gains `特に` — no need for a separate card for the particle form. Applies to `occurrences:` and `seen:` alike.
- Summary window look-and-feel improvements: flatter, more compact layout with clearer rows.
- New `length` search filter — e.g. `length>=3` for words 3 or more characters long, `length=1` for single-character words.
- Performance enhancements, lower memory use, and assorted bug fixes.

## 2026-06-19
- New `seen:N` search term — prioritize words from your recent *daily* occurrence dictionaries (in `user_files/_seen/<YYYY-MM-DD>/`). Matches words appearing in any of the last N days ("seen at all"). Works in the Browse bar, the collection API (AnkiConnect), and config, and honors the occurrence options (prefix matching, etc.).
- Assorted bug fixes (search `or` handling, summary window, custom-term caching, error during sync on close).
- Safer dictionary updating and lower memory use.
- Reorder-on-sync now skips repositioning when the new-card order is already correct, so syncs no longer stay stuck on "changes pending" after an unchanged reorder.
- A sync-triggered reorder now pushes the new card order in a single sync — previously a second manual sync was needed before the new order took effect on other devices.

## 2026-06-04
- New **Summary window** (Tools → Priority Reorder → Show Summary).
- Jiten occurrence-dictionary updating, manual or automatic (`auto_update_dicts`).
- `prefix_matching` and `honorific_folding` occurrence options.
- `kana_normalization` option (treat katakana/hiragana variants as equivalent).
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
