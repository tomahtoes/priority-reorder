"""Unit tests for JitenUpdater: skip gates, metadata stamping, the atomic
extract-and-swap, and the regression that a failed extract must never leave a
dictionary half-deleted. Network is faked; the filesystem is real (tmp_path)."""

import io
import json
import time
import zipfile

import pytest

from updater import JitenUpdater


def _make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, json_data=None, content=b""):
        self._json = json_data
        self.content = content

    def json(self):
        return self._json

    def raise_for_status(self):
        pass


class _FakeSession:
    def __init__(self, search_json=None, zip_bytes=b""):
        self.search_json = search_json or {"suggestions": []}
        self.zip_bytes = zip_bytes
        self.get_urls = []
        self.post_urls = []

    def get(self, url, **kwargs):
        self.get_urls.append(url)
        return _FakeResponse(json_data=self.search_json)

    def post(self, url, **kwargs):
        self.post_urls.append(url)
        return _FakeResponse(content=self.zip_bytes)


JITEN_INDEX = {"title": "T", "author": "Jiten", "url": "https://jiten.moe/x", "revision": "r1"}


@pytest.fixture
def updater(tmp_path):
    u = JitenUpdater()
    u.user_files_dir = str(tmp_path)
    u.session = _FakeSession()
    return u


def _dict_dir(tmp_path, name="MyDict", index=None):
    d = tmp_path / name
    d.mkdir()
    if index is not None:
        (d / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return d


# --- discovery ----------------------------------------------------------------

def test_dict_dirs_ignore_all_and_dot_dirs(updater, tmp_path):
    (tmp_path / "A").mkdir()
    (tmp_path / "all").mkdir()
    (tmp_path / ".A.new").mkdir()  # leftover from an interrupted swap
    assert updater._dict_dirs() == ["A"]
    assert updater.get_dictionary_count() == 1


# --- skip gates -----------------------------------------------------------------

def test_non_jiten_dict_is_skipped_without_network(updater, tmp_path):
    _dict_dir(tmp_path, index={"title": "T", "author": "Someone", "url": "x", "revision": "r1"})
    assert updater.update_dictionaries(manual=True) == (0, 0)
    assert updater.session.get_urls == []
    assert updater.session.post_urls == []


def test_missing_title_is_skipped(updater, tmp_path):
    _dict_dir(tmp_path, index={"author": "Jiten", "url": "x", "revision": "r1"})
    assert updater.update_dictionaries(manual=True) == (0, 0)
    assert updater.session.post_urls == []


def test_daily_cutoff_gate_skips_recently_checked(updater, tmp_path):
    now = int(time.time())
    _dict_dir(tmp_path, index=dict(JITEN_INDEX, last_update_time=now))
    assert updater.update_dictionaries(manual=False, next_day_cutoff=now + 3600) == (0, 0)
    assert updater.session.post_urls == []


# --- same revision: stamp metadata only -------------------------------------------

def test_same_revision_stamps_metadata_without_replacing(updater, tmp_path):
    d = _dict_dir(tmp_path, index=dict(JITEN_INDEX, deckId=7))
    (d / "term_meta_bank_1.json").write_text("[]", encoding="utf-8")
    updater.session = _FakeSession(zip_bytes=_make_zip({"index.json": json.dumps({"revision": "r1"})}))

    assert updater.update_dictionaries(manual=False, next_day_cutoff=0) == (0, 0)

    stamped = json.loads((d / "index.json").read_text(encoding="utf-8"))
    assert stamped["deckId"] == 7
    assert stamped["last_update_time"] > 0
    assert (d / "term_meta_bank_1.json").read_text(encoding="utf-8") == "[]"  # untouched


# --- successful update ---------------------------------------------------------------

def test_successful_update_replaces_contents_and_stamps_index(updater, tmp_path):
    d = _dict_dir(tmp_path, index=dict(JITEN_INDEX, deckId=7))
    (d / "old_file.json").write_text("old", encoding="utf-8")
    updater.session = _FakeSession(zip_bytes=_make_zip({
        "index.json": json.dumps(dict(JITEN_INDEX, revision="r2")),
        "term_meta_bank_1.json": "[]",
    }))

    assert updater.update_dictionaries(manual=True) == (1, 0)

    assert not (d / "old_file.json").exists()
    assert (d / "term_meta_bank_1.json").exists()
    stamped = json.loads((d / "index.json").read_text(encoding="utf-8"))
    assert stamped["revision"] == "r2"
    assert stamped["deckId"] == 7
    assert stamped["last_update_time"] > 0
    # no temp swap dirs left behind
    assert sorted(p.name for p in tmp_path.iterdir()) == ["MyDict"]


# --- B1 regression: failure must not destroy the dictionary --------------------------

def test_failed_extract_preserves_existing_contents(updater, tmp_path, monkeypatch):
    d = _dict_dir(tmp_path, index=dict(JITEN_INDEX, deckId=7))
    (d / "term_meta_bank_1.json").write_text("precious", encoding="utf-8")
    updater.session = _FakeSession(zip_bytes=_make_zip({
        "index.json": json.dumps(dict(JITEN_INDEX, revision="r2")),
        "term_meta_bank_1.json": "[]",
    }))

    def boom(self, path):
        raise OSError("disk full")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", boom)

    assert updater.update_dictionaries(manual=True) == (0, 1)  # failed, not updated

    # The original dictionary is fully intact, and no temp dirs remain.
    assert (d / "term_meta_bank_1.json").read_text(encoding="utf-8") == "precious"
    assert json.loads((d / "index.json").read_text(encoding="utf-8"))["revision"] == "r1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["MyDict"]


# --- deck id resolution -----------------------------------------------------------

def test_resolve_deck_id_prefers_exact_title_match(updater):
    updater.session = _FakeSession(search_json={"suggestions": [
        {"originalTitle": "Other", "deckId": 1},
        {"romajiTitle": "T", "deckId": 2},
    ]})
    assert updater._resolve_deck_id({}, "T") == 2


def test_resolve_deck_id_falls_back_to_first_suggestion(updater):
    updater.session = _FakeSession(search_json={"suggestions": [
        {"originalTitle": "Other", "deckId": 9},
    ]})
    assert updater._resolve_deck_id({}, "T") == 9


def test_resolve_deck_id_uses_stored_id_without_network(updater):
    assert updater._resolve_deck_id({"deckId": 42}, "T") == 42
    assert updater.session.get_urls == []
