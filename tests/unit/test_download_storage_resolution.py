"""Tests for rpc/mixins/download.py's storage-id → path resolution.

Regression coverage for the "always picks the first external mount"
bug: once ``storage.py`` gives every external device a unique
``ext:<name>`` id, ``download.py`` must resolve to the SPECIFIC
device the user picked, not silently substitute whichever mount
happens to be first in ``/proc/mounts``.
"""
from __future__ import annotations

import pytest

from unifideck.rpc.mixins import download as download_mod
from unifideck.utils import mounts


def _mount(mount_point: str, **overrides: object) -> mounts.MountInfo:
    base: dict[str, object] = {
        "device": "/dev/sda1", "mount_point": mount_point, "fstype": "ext4",
        "st_dev": abs(hash(mount_point)) % 100000 + 1, "options": {}, "writable": True,
    }
    base.update(overrides)
    return mounts.MountInfo(**base)  # type: ignore[arg-type]


def test_resolve_by_exact_ext_id_not_first_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _mount("/run/media/deck/FIRST")
    second = _mount("/run/media/deck/SECOND")
    monkeypatch.setattr(mounts, "scan_mounts", lambda *a, **k: [first, second])
    monkeypatch.setattr(mounts, "ensure_games_subdir", lambda mp, uid, gid: f"{mp}/Games")

    result = download_mod._resolve_storage_path(mounts.mount_id(second.mount_point), None)

    assert result == f"{second.mount_point}/Games"


def test_resolve_collided_ext_id_picks_correct_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A basename collision → the suffixed id still resolves the right device."""
    first = _mount("/run/media/deck/GAMES")
    second = _mount("/media/GAMES")
    monkeypatch.setattr(mounts, "scan_mounts", lambda *a, **k: [first, second])
    monkeypatch.setattr(mounts, "ensure_games_subdir", lambda mp, uid, gid: f"{mp}/Games")

    ids = mounts.assign_unique_ids([first, second])
    second_id = next(i for i, m in ids if m.mount_point == second.mount_point)
    assert second_id != "ext:GAMES"  # was disambiguated

    result = download_mod._resolve_storage_path(second_id, None)
    assert result == f"{second.mount_point}/Games"


def test_resolve_legacy_sdcard_picks_first_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _mount("/run/media/deck/FIRST")
    second = _mount("/run/media/deck/SECOND")
    monkeypatch.setattr(mounts, "scan_mounts", lambda *a, **k: [first, second])
    monkeypatch.setattr(mounts, "ensure_games_subdir", lambda mp, uid, gid: f"{mp}/Games")

    result = download_mod._resolve_storage_path("sdcard", None)

    assert result == f"{first.mount_point}/Games"


def test_resolve_unplugged_ext_id_returns_none_not_a_substitute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only_mount = _mount("/run/media/deck/STILLHERE")
    monkeypatch.setattr(mounts, "scan_mounts", lambda *a, **k: [only_mount])

    result = download_mod._resolve_storage_path("ext:GONE", None)

    assert result is None


def test_resolve_internal_unaffected() -> None:
    result = download_mod._resolve_storage_path("internal", None)
    assert result is not None
    assert result.endswith("/Games")


def test_resolve_unknown_storage_type_returns_none() -> None:
    assert download_mod._resolve_storage_path("bogus", None) is None


def test_resolve_none_storage_type_returns_none() -> None:
    assert download_mod._resolve_storage_path(None, None) is None
