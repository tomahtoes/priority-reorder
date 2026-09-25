"""What the release ships, and what it must not.

`collect_files` is a non-recursive glob plus three named files, so the boundary between addon
code and developer tooling is positional: anything at the project root ships. A helper dropped
there by mistake, say a benchmark next to the module it benchmarks, would be packaged into
every release and become importable inside Anki without anyone noticing.
"""

import importlib.util
import os
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files added since the newest built release, each one deliberate. Once a release ships them
# they sit in its zip too, so a stale entry here is harmless.
ADDED_SINCE_LAST_RELEASE = {"kanji_variants.txt", "summary_html.py"}


def _build_release():
    path = os.path.join(ROOT, ".ankiaddon", "build_release.py")
    spec = importlib.util.spec_from_file_location("_build_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shipped():
    return sorted(p.name for p in _build_release().collect_files())


def test_developer_tooling_is_not_packaged(shipped):
    for directory in ("tools", "tests", ".ankiaddon", "anti-slop"):
        for name in os.listdir(os.path.join(ROOT, directory)) if os.path.isdir(
                os.path.join(ROOT, directory)) else []:
            if name.endswith(".py"):
                assert name not in shipped, "%s/%s would ship" % (directory, name)


def test_docs_and_config_are_not_packaged(shipped):
    for name in ("README.md", "CHANGELOG.md", "AGENTS.md", "CLAUDE.md"):
        assert name not in shipped


def test_every_shipped_python_file_sits_at_the_root(shipped):
    for name in shipped:
        if name.endswith(".py"):
            assert os.path.isfile(os.path.join(ROOT, name))


def test_shipped_set_matches_the_last_release():
    """The packaged set is pinned against the newest built .ankiaddon.

    A new root-level module is a deliberate act and should show up here as a failing diff, not
    slip in unremarked. Update the expectation in the same commit that adds the module."""
    releases = sorted(
        name for name in os.listdir(os.path.join(ROOT, ".ankiaddon"))
        if name.endswith(".ankiaddon")
    )
    if not releases:
        pytest.skip("no built release to compare against")
    newest = max(releases, key=lambda n: os.path.getmtime(
        os.path.join(ROOT, ".ankiaddon", n)))
    with zipfile.ZipFile(os.path.join(ROOT, ".ankiaddon", newest)) as zf:
        previous = sorted(zf.namelist())
    current = sorted(p.name for p in _build_release().collect_files())
    previous = sorted(set(previous) | (ADDED_SINCE_LAST_RELEASE & set(current)))
    assert current == previous, (
        "the packaged file set changed against %s: added %s, removed %s"
        % (newest, sorted(set(current) - set(previous)), sorted(set(previous) - set(current)))
    )
