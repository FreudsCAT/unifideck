"""Tests for core/exe_finder.py (OP-04c)."""
from __future__ import annotations

import os

from unifideck.core.exe_finder import WRAPPER_EXES, ExeFinder


def test_find_returns_none_for_missing_dir():
    ef = ExeFinder()
    assert ef.find("/nonexistent_xyz_test") is None


def test_find_returns_none_for_empty_dir(tmp_path):
    ef = ExeFinder()
    assert ef.find(str(tmp_path)) is None


def test_find_skips_wrapper_exes(tmp_path):
    """Files in WRAPPER_EXES should be excluded from results."""
    ef = ExeFinder()
    (tmp_path / "uninstall.exe").write_bytes(b"\x00" * 1000)
    assert ef.find(str(tmp_path)) is None


def test_find_prefers_hint_match(tmp_path):
    ef = ExeFinder()
    game = tmp_path / "game.exe"
    game.write_bytes(b"\x00" * 100)
    other = tmp_path / "other.exe"
    other.write_bytes(b"\x00" * 10000)  # bigger but no hint match
    result = ef.find(str(tmp_path), hints=["game.exe"])
    assert result is not None
    assert "game.exe" in result


def test_find_prefers_shallow_depth(tmp_path):
    ef = ExeFinder()
    shallow = tmp_path / "game.exe"
    shallow.write_bytes(b"\x00" * 100)
    deep = tmp_path / "sub" / "deep" / "game2.exe"
    deep.parent.mkdir(parents=True)
    deep.write_bytes(b"\x00" * 100)
    result = ef.find(str(tmp_path))
    assert result is not None
    assert "sub" not in os.path.relpath(result, str(tmp_path))


def test_wrapper_exes_count():
    assert len(WRAPPER_EXES) == 21


def test_walk_depth_limit(tmp_path):
    """Depth > 3 should be pruned."""
    ef = ExeFinder()
    deep = tmp_path / "a" / "b" / "c" / "d" / "game.exe"
    deep.parent.mkdir(parents=True)
    deep.write_bytes(b"\x00" * 1000)
    # Only game at depth 4 — should not be found
    assert ef.find(str(tmp_path)) is None
