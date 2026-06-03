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
from typing import Optional, Dict, Any

from .config_manager import get_config
from .dictionary_manager import get_occurrence_index, get_combined_occurrence_index, _load_term_meta_raw

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

    def get_dictionary_count(self) -> int:
        if not os.path.isdir(self.user_files_dir):
            return 0
        return len([d for d in os.listdir(self.user_files_dir) if d != "all" and os.path.isdir(os.path.join(self.user_files_dir, d))])

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

            # 1. Fetch exact Deck Id from Search Suggestions
            target_deck_id = index_data.get("deckId")

            if not target_deck_id:
                search_url = f"{self.api_base}/search-suggestions?query={urllib.parse.quote(title)}"
                try:
                    search_res = self.session.get(search_url).json()
                except Exception:
                    return -1

                suggestions = search_res.get("suggestions", [])
                for s in suggestions:
                    if s.get("originalTitle") == title or s.get("romajiTitle") == title or s.get("englishTitle") == title:
                        target_deck_id = s.get("deckId")
                        break

                if not target_deck_id and suggestions:
                    target_deck_id = suggestions[0].get("deckId")

            if not target_deck_id:
                return 0

            # 2. Download ZIP into memory
            download_url = f"{self.api_base}/{target_deck_id}/download"
            try:
                res = self.session.post(download_url, json={"format": 5, "downloadType": 6})
                res.raise_for_status()
                zip_data = res.content
            except Exception:
                return -1

            with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
                if "index.json" in z.namelist():
                    with z.open("index.json") as f:
                        remote_index = json.load(f)
                    remote_revision = remote_index.get("revision")

                    if not manual and remote_revision == local_revision:
                        index_data["last_update_time"] = current_time
                        if target_deck_id:
                            index_data["deckId"] = target_deck_id
                        try:
                            with open(os.path.join(dict_path, "index.json"), "w", encoding="utf-8") as f:
                                json.dump(index_data, f, ensure_ascii=False, separators=(',', ':'))
                        except Exception:
                            pass
                        return 0
                else:
                    return 0

                # Clear current directory contents
                for filename in os.listdir(dict_path):
                    filepath = os.path.join(dict_path, filename)
                    try:
                        if os.path.isfile(filepath) or os.path.islink(filepath):
                            os.unlink(filepath)
                        elif os.path.isdir(filepath):
                            shutil.rmtree(filepath)
                    except Exception:
                        pass

                # Extract new contents
                z.extractall(dict_path)

                # Save deckId and last_update_time to index.json
                try:
                    new_index_path = os.path.join(dict_path, "index.json")
                    if os.path.exists(new_index_path):
                        with open(new_index_path, "r", encoding="utf-8") as f:
                            new_index_data = json.load(f)
                        new_index_data["deckId"] = target_deck_id
                        new_index_data["last_update_time"] = current_time
                        with open(new_index_path, "w", encoding="utf-8") as f:
                            json.dump(new_index_data, f, ensure_ascii=False, separators=(',', ':'))
                except Exception:
                    pass

                return 1

        except Exception:
            return -1

    def update_dictionaries(self, manual: bool = False, next_day_cutoff: int = 0) -> tuple[int, int]:
        if not os.path.isdir(self.user_files_dir):
            return 0, 0

        dict_dirs = [d for d in os.listdir(self.user_files_dir) if d != "all" and os.path.isdir(os.path.join(self.user_files_dir, d))]
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
            except Exception:
                continue

            res = self._update_single_dict(dict_path, index_data, manual, next_day_cutoff)
            if res == 1:
                updated_count += 1
            elif res == -1:
                failed_count += 1

        self.clear_caches()

        return updated_count, failed_count

    def clear_caches(self) -> None:
        # Order matters: clear the raw-JSON cache too, otherwise rebuilt indexes
        # re-read stale dictionary contents and the update has no effect until
        # Anki is restarted.
        _load_term_meta_raw.cache_clear()
        get_occurrence_index.cache_clear()
        get_combined_occurrence_index.cache_clear()
