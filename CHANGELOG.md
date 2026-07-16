# Changelog

## 2026-07-15
- `kanji:new` now takes an optional per-kanji target: `kanji:new[3]>=1` matches words with at least one kanji that fewer than 3 of your learned words contain. A kanji counts as "new" until the target number of learned words contain it; plain `kanji:new` is unchanged (equivalent to `kanji:new[1]`).
- `prefix_matching` now also credits single-kanji cards from particle-linked phrase entries (e.g. `手`/`て` gains `手を貸す`'s count) when the phrase's reading confirms the card's reading. Applies to `occurrences:` and `seen:` alike.
- Summary window look-and-feel improvements: flatter, more compact layout with clearer rows.
- New `length` search filter — e.g. `length>=3` for words 3 or more characters long, `length=1` for single-character words.
- Minor changes to improve reorder speeds.

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
