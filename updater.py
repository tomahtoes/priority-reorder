import os
import json
import urllib.parse
import zipfile
import shutil
import io
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Any, Optional

try:  # inside Anki: isolated package namespace
    from .dictionary_manager import get_occurrence_index, get_combined_occurrence_index, SEEN_FOLDER
except ImportError:  # pytest / flat-import context
    from dictionary_manager import get_occurrence_index, get_combined_occurrence_index, SEEN_FOLDER

class JitenUpdater:
    def __init__(self) -> None:
        self.user_files_dir = os.path.join(os.path.dirname(__file__), "user_files")
        self.api_base = "https://api.jiten.moe/api/media-deck"
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0'})
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)

    def _dict_dirs(self) -> list:
        """Dictionary directory names in user_files ('all', the reserved '_seen'
        folder, and dot-prefixed temp dirs from interrupted swaps excluded)."""
        if not os.path.isdir(self.user_files_dir):
            return []
        return [
            d for d in os.listdir(self.user_files_dir)
            if d != "all" and d != SEEN_FOLDER and not d.startswith(".")
            and os.path.isdir(os.path.join(self.user_files_dir, d))
        ]

    def get_dictionary_count(self) -> int:
        return len(self._dict_dirs())

    def _resolve_deck_id(self, index_data: dict, title: str) -> Optional[Any]:
        """Deck id stored in index.json if present, else looked up via the search
        API (exact title match on any variant, falling back to the top suggestion).
        Returns None when nothing matches; raises on network failure."""
        deck_id = index_data.get("deckId")
        if deck_id:
            return deck_id

        search_url = f"{self.api_base}/search-suggestions?query={urllib.parse.quote(title)}"
        search_res = self.session.get(search_url).json()
        suggestions = search_res.get("suggestions", [])
        for s in suggestions:
            if s.get("originalTitle") == title or s.get("romajiTitle") == title or s.get("englishTitle") == title:
                return s.get("deckId")
        if suggestions:
            return suggestions[0].get("deckId")
        return None

    def _download_zip(self, deck_id: Any) -> bytes:
        res = self.session.post(
            f"{self.api_base}/{deck_id}/download",
            json={"format": 5, "downloadType": 6},
        )
        res.raise_for_status()
        return res.content

    def _write_index_json(self, dict_path: str, data: dict) -> None:
        try:
            with open(os.path.join(dict_path, "index.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        except Exception as e:
            print(f"[priority-reorder] failed to write index.json in {dict_path}: {e}")

    def _clear_directory(self, path: str) -> None:
        for filename in os.listdir(path):
            filepath = os.path.join(path, filename)
            try:
                if os.path.isfile(filepath) or os.path.islink(filepath):
                    os.unlink(filepath)
                elif os.path.isdir(filepath):
                    shutil.rmtree(filepath)
            except Exception as e:
                print(f"[priority-reorder] failed to remove {filepath}: {e}")

    def _replace_dict_contents(self, dict_path: str, z: zipfile.ZipFile) -> None:
        """Extract `z` into a temp sibling dir first and swap it in afterwards, so
        a failed extract (or Anki shutting down mid-update) never leaves the
        dictionary half-deleted."""
        parent = os.path.dirname(dict_path)
        base = os.path.basename(dict_path)
        tmp_new = os.path.join(parent, f".{base}.new")
        tmp_old = os.path.join(parent, f".{base}.old")
        for leftover in (tmp_new, tmp_old):  # from a previously interrupted run
            if os.path.isdir(leftover):
                shutil.rmtree(leftover, ignore_errors=True)

        os.makedirs(tmp_new)
        try:
            z.extractall(tmp_new)
            try:
                os.rename(dict_path, tmp_old)
            except OSError as e:
                # Rename blocked (e.g. a file inside is locked): replace in place.
                print(f"[priority-reorder] falling back to in-place swap for {dict_path}: {e}")
                self._clear_directory(dict_path)
                for name in os.listdir(tmp_new):
                    shutil.move(os.path.join(tmp_new, name), os.path.join(dict_path, name))
                return
            try:
                os.rename(tmp_new, dict_path)
            except OSError:
                os.rename(tmp_old, dict_path)  # restore the original contents
                raise
            shutil.rmtree(tmp_old, ignore_errors=True)
        finally:
            if os.path.isdir(tmp_new):
                shutil.rmtree(tmp_new, ignore_errors=True)

    def _update_single_dict(self, dict_path: str, index_data: dict, manual: bool, next_day_cutoff: int) -> int:
        """Returns 1 if updated, 0 if skipped, -1 if failed."""
        try:
            author = index_data.get("author", "")
            url = index_data.get("url", "")
            if "Jiten" not in author and "jiten.moe" not in url:
                return 0

            title = index_data.get("title")
            local_revision = index_data.get("revision", "")
            if not title:
                return 0

            current_time = int(time.time())

            if not manual and next_day_cutoff > 0:
                last_update_time = index_data.get("last_update_time", 0)
                if last_update_time >= next_day_cutoff - 86400:
                    return 0

            target_deck_id = self._resolve_deck_id(index_data, title)
            if not target_deck_id:
                return 0

            zip_data = self._download_zip(target_deck_id)

            with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
                if "index.json" not in z.namelist():
                    return 0
                with z.open("index.json") as f:
                    remote_index = json.load(f)

                if not manual and remote_index.get("revision") == local_revision:
                    # Already current: just stamp deckId + check time.
                    index_data["last_update_time"] = current_time
                    index_data["deckId"] = target_deck_id
                    self._write_index_json(dict_path, index_data)
                    return 0

                self._replace_dict_contents(dict_path, z)

            # Stamp deckId and last_update_time into the freshly extracted index.json
            new_index_path = os.path.join(dict_path, "index.json")
            try:
                with open(new_index_path, "r", encoding="utf-8") as f:
                    new_index_data = json.load(f)
            except Exception as e:
                print(f"[priority-reorder] failed to read new index.json in {dict_path}: {e}")
                return 1  # the dictionary itself was updated successfully
            new_index_data["deckId"] = target_deck_id
            new_index_data["last_update_time"] = current_time
            self._write_index_json(dict_path, new_index_data)

            return 1

        except Exception as e:
            print(f"[priority-reorder] dictionary update failed for {dict_path}: {e}")
            return -1

    def update_dictionaries(self, manual: bool = False, next_day_cutoff: int = 0) -> tuple[int, int]:
        dict_dirs = self._dict_dirs()
        if not dict_dirs:
            return 0, 0

        updated_count = 0
        failed_count = 0

        for dict_name in dict_dirs:
            dict_path = os.path.join(self.user_files_dir, dict_name)
            index_path = os.path.join(dict_path, "index.json")

            if not os.path.isfile(index_path):
                continue

            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index_data = json.load(f)
            except Exception as e:
                print(f"[priority-reorder] failed to read {index_path}: {e}")
                continue

            res = self._update_single_dict(dict_path, index_data, manual, next_day_cutoff)
            if res == 1:
                updated_count += 1
            elif res == -1:
                failed_count += 1

        self.clear_caches()

        return updated_count, failed_count

    def clear_caches(self) -> None:
        # Index builds re-read the term banks from disk (the raw JSON is not
        # cached), so dropping the built indexes is enough for updated
        # dictionary contents to take effect without restarting Anki.
        get_occurrence_index.cache_clear()
        get_combined_occurrence_index.cache_clear()
