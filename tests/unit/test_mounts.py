"""Tests for utils/mounts.py — shared external-storage mount enumeration.

Covers the FUSE-mounted-external-storage regression: a mount owned
by a uid other than this process's (the ``uid=``/``gid=`` options
ntfs-3g/fuse-exfat mounts carry) must be reachable via a demoted
subprocess rather than silently excluded, and — separately — a
permission-denied mount with no such option must be excluded
cleanly rather than raising (the confirmed uncaught-``PermissionError``
bug in the pre-fix per-file scanners).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from unifideck.utils import mounts


def _write_mounts(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "proc-mounts"
    p.write_text("\n".join(lines) + "\n")
    return p


# ─── parse_mount_options / is_eligible_type / stat_dev ─────────


def test_parse_mount_options_extracts_uid_gid() -> None:
    assert mounts.parse_mount_options("rw,nosuid,uid=1000,gid=1000,umask=0022") == {
        "rw": "",
        "nosuid": "",
        "uid": "1000",
        "gid": "1000",
        "umask": "0022",
    }


def test_parse_mount_options_no_uid_option() -> None:
    assert mounts.parse_mount_options("rw,relatime") == {"rw": "", "relatime": ""}


def test_parse_mount_options_empty_string_never_raises() -> None:
    assert mounts.parse_mount_options("") == {}


@pytest.mark.parametrize("fstype", ["exfat", "ntfs3", "fuseblk", "vfat", "btrfs", "xfs", "ext4"])
def test_is_eligible_type_allows_real_filesystems(fstype: str) -> None:
    assert mounts.is_eligible_type(fstype, "/run/media/deck/CARD") is True


@pytest.mark.parametrize("fstype", ["tmpfs", "proc", "sysfs", "autofs", "overlay"])
def test_is_eligible_type_skips_virtual_fstypes(fstype: str) -> None:
    assert mounts.is_eligible_type(fstype, "/run/media/deck/CARD") is False


@pytest.mark.parametrize("prefix", ["/dev/", "/sys/", "/proc/", "/run/user/1000/"])
def test_is_eligible_type_skips_virtual_prefixes(prefix: str) -> None:
    assert mounts.is_eligible_type("ext4", prefix + "x") is False


def test_stat_dev_returns_zero_on_missing_path(tmp_path: Path) -> None:
    assert mounts.stat_dev(str(tmp_path / "does-not-exist")) == 0


def test_stat_dev_real_path(tmp_path: Path) -> None:
    assert mounts.stat_dev(str(tmp_path)) == tmp_path.stat().st_dev


# ─── mount_id / is_sdcard_source ────────────────────────────────


def test_mount_id_unique_for_two_simultaneous_external_mounts() -> None:
    assert mounts.mount_id("/run/media/deck/SDCARD") == "ext:SDCARD"
    assert mounts.mount_id("/run/media/deck/USBDRIVE") == "ext:USBDRIVE"
    assert mounts.mount_id("/run/media/deck/SDCARD") != mounts.mount_id(
        "/run/media/deck/USBDRIVE",
    )


def test_is_sdcard_source_detects_mmcblk() -> None:
    assert mounts.is_sdcard_source("/dev/mmcblk0p1") is True
    assert mounts.is_sdcard_source("/dev/sda1") is False


# ─── dedupe_by_device ───────────────────────────────────────────


def _mount_info(mount_point: str, st_dev: int, **overrides: object) -> mounts.MountInfo:
    base: dict[str, object] = {
        "device": "/dev/sda1", "mount_point": mount_point, "fstype": "ext4",
        "st_dev": st_dev, "options": {}, "writable": True,
    }
    base.update(overrides)
    return mounts.MountInfo(**base)  # type: ignore[arg-type]


def test_dedupe_by_device_collapses_same_st_dev() -> None:
    a = _mount_info("/mnt/a", 42)
    b = _mount_info("/mnt/b", 42)
    assert mounts.dedupe_by_device([a, b]) == [a]


def test_dedupe_by_device_keeps_distinct_zero_entries() -> None:
    a = _mount_info("/mnt/a", 0)
    b = _mount_info("/mnt/b", 0)
    assert mounts.dedupe_by_device([a, b]) == [a, b]


# ─── run_demoted ────────────────────────────────────────────────


def test_run_demoted_happy_path_real_subprocess() -> None:
    proc = mounts.run_demoted(["true"], os.geteuid())
    assert proc is not None
    assert proc.returncode == 0


def test_run_demoted_missing_binary_returns_none() -> None:
    assert mounts.run_demoted(["/nonexistent/binary-xyz"], os.geteuid()) is None


def test_run_demoted_swallows_any_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_perm(*args: object, **kwargs: object) -> None:
        raise PermissionError("nope")

    monkeypatch.setattr(subprocess, "run", raise_perm)
    assert mounts.run_demoted(["true"], 1000) is None


# ─── ensure_games_subdir ────────────────────────────────────────


def test_ensure_games_subdir_direct_when_no_effective_uid(tmp_path: Path) -> None:
    result = mounts.ensure_games_subdir(str(tmp_path), None)
    assert result == str(tmp_path / "Games")
    assert (tmp_path / "Games").is_dir()


def test_ensure_games_subdir_demoted_when_effective_uid_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = mounts.ensure_games_subdir(str(tmp_path), 1000, 1000)
    assert result == str(tmp_path / "Games")
    assert not (tmp_path / "Games").is_dir(), "must not mkdir directly once demoted"
    assert len(calls) == 1
    assert calls[0][1]["user"] == 1000
    assert calls[0][1]["group"] == 1000


def test_ensure_games_subdir_returns_mount_point_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr=""),
    )
    assert mounts.ensure_games_subdir(str(tmp_path), 1000) == str(tmp_path)


# ─── mount_is_dir / mount_child_dirs ────────────────────────────


def test_mount_is_dir_direct(tmp_path: Path) -> None:
    assert mounts.mount_is_dir(str(tmp_path), None) is True
    assert mounts.mount_is_dir(str(tmp_path / "nope"), None) is False


def test_mount_is_dir_demoted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""),
    )
    assert mounts.mount_is_dir("/some/fuse/mount", 1000) is True


def test_mount_child_dirs_direct(tmp_path: Path) -> None:
    (tmp_path / "Games").mkdir()
    (tmp_path / "GOG Games").mkdir()
    (tmp_path / "afile.txt").write_text("x")
    (tmp_path / "link").symlink_to(tmp_path / "Games")
    children = {p.name for p in mounts.mount_child_dirs(str(tmp_path), None)}
    assert children == {"Games", "GOG Games"}


def test_mount_child_dirs_demoted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout="/mnt/card/Games\n/mnt/card/GOG Games\n", stderr="",
        ),
    )
    children = mounts.mount_child_dirs("/mnt/card", 1000)
    assert children == [Path("/mnt/card/Games"), Path("/mnt/card/GOG Games")]


# ─── scan_mounts: the end-to-end regression tests ───────────────


def test_scan_mounts_direct_access_no_uid_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-FUSE mount, no uid= option: today's fast path, no demotion."""
    ext = tmp_path / "ext-drive"
    ext.mkdir()
    mounts_file = _write_mounts(tmp_path, [
        f"/dev/sda1 {ext} ext4 rw,relatime 0 0",
    ])

    called = False

    def fail_if_called(*a: object, **k: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("subprocess.run must not be called for a directly-accessible mount")

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    # A sentinel home_dev distinct from tmp_path's real st_dev — tmp_path
    # and any subdir of it share one filesystem, so a real "home" subdir
    # would collide with ext-drive's st_dev and get excluded for the
    # wrong reason.
    result = mounts.scan_mounts(999999, mounts_path=mounts_file, require_writable=True)
    assert not called
    assert len(result) == 1
    assert result[0].mount_point == str(ext)
    assert result[0].effective_uid is None
    assert result[0].writable is True


def test_scan_mounts_excludes_home_device(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    mounts_file = _write_mounts(tmp_path, [
        f"/dev/nvme0n1p8 {home} ext4 rw,relatime 0 0",
    ])
    result = mounts.scan_mounts(mounts.stat_dev(str(home)), mounts_path=mounts_file)
    assert result == []


def test_scan_mounts_permission_denied_no_uid_option_excludes_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: must not raise PermissionError out of scan_mounts."""
    mounts_file = _write_mounts(tmp_path, [
        "/dev/sdb1 /mnt/weird ext4 rw,relatime 0 0",
    ])

    def raise_denied(self: Path) -> bool:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "is_dir", raise_denied)

    result = mounts.scan_mounts(999999, mounts_path=mounts_file)
    assert result == []


def test_scan_mounts_fuse_uid_mismatch_demotes_and_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mounts_file = _write_mounts(tmp_path, [
        "/dev/sdb1 /run/media/deck/NTFSCARD fuseblk rw,uid=1000,gid=1000 0 0",
    ])

    monkeypatch.setattr(os, "geteuid", lambda: 0)

    def deny_direct(self: Path) -> bool:
        return False

    monkeypatch.setattr(Path, "is_dir", deny_direct)

    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        if argv[:2] == ["test", "-d"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ["test", "-w"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ["stat", "-c"]:
            return subprocess.CompletedProcess(argv, 0, stdout="99\n", stderr="")
        raise AssertionError(f"unexpected demoted call: {argv}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = mounts.scan_mounts(1, mounts_path=mounts_file, require_writable=True)
    assert len(result) == 1
    info = result[0]
    assert info.effective_uid == 1000
    assert info.effective_gid == 1000
    assert info.writable is True
    assert info.st_dev == 99
    assert all(c[0][0] in ("test", "stat") for c in calls)
    assert all(c[1].get("user") == 1000 and c[1].get("group") == 1000 for c in calls)


def test_scan_mounts_fuse_uid_mismatch_demotion_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mounts_file = _write_mounts(tmp_path, [
        "/dev/sdb1 /run/media/deck/NTFSCARD fuseblk rw,uid=1000,gid=1000 0 0",
    ])
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr=""),
    )

    result = mounts.scan_mounts(1, mounts_path=mounts_file)
    assert result == []


def test_scan_mounts_require_writable_false_accepts_readonly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ext = tmp_path / "ro-drive"
    ext.mkdir()
    mounts_file = _write_mounts(tmp_path, [
        f"/dev/sda1 {ext} ext4 ro,relatime 0 0",
    ])
    monkeypatch.setattr(os, "access", lambda *a, **k: False)

    result = mounts.scan_mounts(999999, mounts_path=mounts_file, require_writable=False)
    assert len(result) == 1
    assert result[0].writable is False

    result_strict = mounts.scan_mounts(999999, mounts_path=mounts_file, require_writable=True)
    assert result_strict == []


def test_scan_mounts_two_simultaneous_external_mounts_get_distinct_ids(
    tmp_path: Path,
) -> None:
    sd = tmp_path / "SDCARD"
    sd.mkdir()
    usb = tmp_path / "USBDRIVE"
    usb.mkdir()
    mounts_file = _write_mounts(tmp_path, [
        f"/dev/mmcblk0p1 {sd} ext4 rw,relatime 0 0",
        f"/dev/sda1 {usb} ext4 rw,relatime 0 0",
    ])

    result = mounts.scan_mounts(999999, mounts_path=mounts_file)
    ids = {mounts.mount_id(m.mount_point) for m in result}
    assert len(result) == 2
    assert len(ids) == 2


def test_scan_mounts_missing_file_returns_empty(tmp_path: Path) -> None:
    assert mounts.scan_mounts(0, mounts_path=tmp_path / "nope") == []


def test_assign_unique_ids_keeps_bare_id_without_collision() -> None:
    a = _mount_info("/run/media/deck/SDCARD", st_dev=11)
    b = _mount_info("/run/media/deck/USB", st_dev=22)
    ids = dict(mounts.assign_unique_ids([a, b]))
    assert set(ids) == {"ext:SDCARD", "ext:USB"}


def test_assign_unique_ids_disambiguates_same_basename() -> None:
    """Two distinct devices sharing a mount-point basename get unique ids."""
    a = _mount_info("/run/media/deck/GAMES", st_dev=11)
    b = _mount_info("/media/GAMES", st_dev=22)
    pairs = mounts.assign_unique_ids([a, b])
    ids = [i for i, _ in pairs]
    assert ids == ["ext:GAMES", "ext:GAMES-22"]  # first bare, later suffixed
    assert len(set(ids)) == 2


def test_assign_unique_ids_stable_across_repeat_calls() -> None:
    """Enumerator + resolver must derive the same ids from the same list."""
    a = _mount_info("/run/media/deck/GAMES", st_dev=11)
    b = _mount_info("/media/GAMES", st_dev=22)
    assert mounts.assign_unique_ids([a, b]) == mounts.assign_unique_ids([a, b])
