"""Tests for utils/paths.py's external-mount game discovery.

Regression coverage for the "two layers deep" subtlety: a FUSE mount
that's only visible via a demoted uid at enumeration time is still
invisible to root for every subsequent filesystem op, including the
per-subdirectory ``Games/``/``GOG Games/`` discovery. Without
threading ``effective_uid`` through ``_collect_game_dirs``/
``_collect_mount_game_dirs``, ``get_all_game_directories()`` would
correctly see the mount but silently find zero installed games on
it — reintroducing the original bug one layer down.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from unifideck.utils import paths as paths_mod
from unifideck.utils import mounts


def _mount(mount_point: str, effective_uid: int | None = None, **overrides: object) -> mounts.MountInfo:
    base: dict[str, object] = {
        "device": "/dev/sda1", "mount_point": mount_point, "fstype": "ext4",
        "st_dev": abs(hash(mount_point)) % 100000 + 1, "options": {}, "writable": True,
        "effective_uid": effective_uid,
    }
    base.update(overrides)
    return mounts.MountInfo(**base)  # type: ignore[arg-type]


def test_scan_external_mounts_direct_access_finds_games_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mount_point = tmp_path / "ext-drive"
    (mount_point / "Games").mkdir(parents=True)
    m = _mount(str(mount_point), effective_uid=None)
    monkeypatch.setattr(mounts, "scan_mounts", lambda *a, **k: [m])

    found = paths_mod._scan_external_mounts()

    assert str(mount_point / "Games") in found


def test_scan_external_mounts_fuse_uid_finds_games_dir_via_demotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two-layers-deep regression test.

    Enumeration says this mount is only visible via uid=1000; the
    per-subdirectory check must ALSO go through a demoted subprocess,
    not silently return nothing just because root can't stat it.
    """
    m = _mount("/run/media/deck/NTFSCARD", effective_uid=1000)
    monkeypatch.setattr(mounts, "scan_mounts", lambda *a, **k: [m])

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["test", "-d"] and argv[2] == "/run/media/deck/NTFSCARD/Games":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ["test", "-d"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        if argv[:2] == ["test", "-L"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")  # not a symlink
        if argv[0] == "find":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected demoted call: {argv}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    found = paths_mod._scan_external_mounts()

    assert found == ["/run/media/deck/NTFSCARD/Games"]


def test_scan_external_mounts_fuse_uid_demotion_fails_yields_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = _mount("/run/media/deck/NTFSCARD", effective_uid=1000)
    monkeypatch.setattr(mounts, "scan_mounts", lambda *a, **k: [m])
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr=""),
    )

    assert paths_mod._scan_external_mounts() == []


def test_get_all_game_directories_includes_internal_and_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ext_mount = tmp_path / "ext-drive"
    (ext_mount / "GOG Games").mkdir(parents=True)
    m = _mount(str(ext_mount), effective_uid=None)
    monkeypatch.setattr(mounts, "scan_mounts", lambda *a, **k: [m])

    result = paths_mod.get_all_game_directories(None)

    assert str(ext_mount / "GOG Games") in result
