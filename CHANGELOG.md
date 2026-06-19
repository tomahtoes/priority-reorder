# Changelog

## 2026-06-19
- New `seen:N` search term — prioritize words from your recent *daily* occurrence dictionaries (in `user_files/_seen/<YYYY-MM-DD>/`). Matches words appearing in any of the last N days ("seen at all"). Works in the Browse bar, the collection API (AnkiConnect), and config, and honors the occurrence options (prefix matching, etc.).
- Assorted bug fixes (search `or` handling, stats window, custom-term caching, error during sync on close).
- Safer dictionary updating and lower memory use.

## 2026-06-04
- New **Stats window** (Tools → Priority Reorder → Show Stats).
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
