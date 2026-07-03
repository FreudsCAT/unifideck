"""Regression: install detection must survive an unreadable games root.

A configured games root can contain a directory we can't stat (a microSD
``lost+found``, a permission-restricted mount). Before the fix, the
unguarded ``base.is_dir()`` in ``walk_install_candidates`` raised
``PermissionError``, which propagated out of the whole library fetch and
hid the user's entire Ubisoft library. The walk must now skip what it
can't read and keep scanning.
"""
from __future__ import annotations

from pathlib import Path

from unifideck.stores.ubisoft.library.detection_helpers import (
    walk_install_candidates,
)


def test_walk_skips_permission_error_root(monkeypatch, tmp_path):
    good = tmp_path / "good"
    (good / "GameA").mkdir(parents=True)
    bad = tmp_path / "bad"

    real_is_dir = Path.is_dir

    def fake_is_dir(self: Path) -> bool:
        if self == bad:
            raise PermissionError("stat denied")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    results = list(walk_install_candidates([str(bad), str(good)]))

    assert {name for _path, name in results} == {"GameA"}


def test_walk_skips_permission_error_on_entry(monkeypatch, tmp_path):
    """A single unreadable entry inside a good root is skipped, not fatal."""
    root = tmp_path / "root"
    (root / "Good Game").mkdir(parents=True)
    bad_entry = root / "Bad Game"
    bad_entry.mkdir()

    real_is_dir = Path.is_dir

    def fake_is_dir(self: Path) -> bool:
        if self == bad_entry:
            raise PermissionError("stat denied")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)

    results = list(walk_install_candidates([str(root)]))

    assert {name for _path, name in results} == {"Good Game"}


def test_walk_missing_root_is_noop(tmp_path):
    assert list(walk_install_candidates([str(tmp_path / "nope")])) == []
