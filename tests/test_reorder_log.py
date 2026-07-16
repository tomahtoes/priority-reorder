"""Unit tests for the opt-in timings log (reorder_log.append_timings_line):
formatting, rotation, and the must-never-raise guarantee."""

import reorder_log


def _use_tmp_log(monkeypatch, tmp_path):
    path = tmp_path / "_timings.log"
    monkeypatch.setattr(reorder_log, "_timings_log_path", lambda: str(path))
    return path


def test_append_creates_file_with_timestamp_and_timings(monkeypatch, tmp_path):
    path = _use_tmp_log(monkeypatch, tmp_path)
    reorder_log.append_timings_line("2026-07-14 12:00:00", {"fc": 1.2, "total": 8.5})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["2026-07-14 12:00:00  fc=1.2ms total=8.5ms"]


def test_append_rotates_to_max_lines_keeping_newest(monkeypatch, tmp_path):
    path = _use_tmp_log(monkeypatch, tmp_path)
    cap = reorder_log._TIMINGS_LOG_MAX_LINES
    path.write_text("\n".join(f"old {i}" for i in range(cap + 50)) + "\n", encoding="utf-8")

    reorder_log.append_timings_line("2026-07-14 12:00:00", {"total": 1.0})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == cap
    assert lines[-1] == "2026-07-14 12:00:00  total=1.0ms"
    assert lines[0] == f"old {51}"  # oldest lines dropped


def test_append_swallows_failures(monkeypatch):
    def boom():
        raise RuntimeError("no disk for you")

    monkeypatch.setattr(reorder_log, "_timings_log_path", boom)
    reorder_log.append_timings_line("2026-07-14 12:00:00", {"total": 1.0})  # must not raise
