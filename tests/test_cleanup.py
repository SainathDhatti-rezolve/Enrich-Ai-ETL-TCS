"""Tests for src.cleanup.

Cleanup deletes per-run working directories after a successful upload. It must
delete only the targeted directories and never touch sibling runs.
"""

from __future__ import annotations

from pathlib import Path

from src.cleanup import cleanup_run_directories, remove_directory


def _make_run_dir(base: Path, run_id: str) -> Path:
    run_dir = base / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "file.csv").write_text("data", encoding="utf-8")
    return run_dir


def test_remove_directory_deletes_dir_and_contents(tmp_path: Path) -> None:
    run_dir = _make_run_dir(tmp_path, "20260806_100000")
    assert run_dir.exists()

    removed = remove_directory(run_dir)

    assert removed is True
    assert not run_dir.exists()


def test_remove_directory_missing_returns_false(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"

    assert remove_directory(missing) is False


def test_cleanup_run_directories_removes_all_given(tmp_path: Path) -> None:
    incoming = _make_run_dir(tmp_path / "incoming", "20260806_100000")
    outgoing = _make_run_dir(tmp_path / "outgoing", "20260806_100000")

    cleanup_run_directories(incoming, outgoing)

    assert not incoming.exists()
    assert not outgoing.exists()


def test_cleanup_leaves_sibling_run_directories_untouched(tmp_path: Path) -> None:
    incoming_base = tmp_path / "incoming"
    this_run = _make_run_dir(incoming_base, "20260806_100000")
    other_run = _make_run_dir(incoming_base, "20260805_100000")

    cleanup_run_directories(this_run)

    assert not this_run.exists()
    assert other_run.exists()
    assert (other_run / "file.csv").exists()
