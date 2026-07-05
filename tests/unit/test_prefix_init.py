"""Unit tests for compat/prefix_init — proton-change reset + first init.

Covers the deterministic logic: Proton family classification, the
reset-vs-notify decision on a Proton change, the destructive reset
(wipe-but-preserve + user-data backup), and the marker bookkeeping.
The umu ``createprefix`` subprocess path is exercised only for the
fast "already initialised → skip" case (no subprocess).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from unifideck.launcher.proton.compat import prefix_init as pi


@pytest.fixture
def toast_spy(monkeypatch):
    """Capture launcher_toast(i18n_key, **kw) calls."""
    spy = MagicMock()
    monkeypatch.setattr(pi, "launcher_toast", spy)
    return spy


def _plan(prefix_root: Path, tool: str):
    """Minimal ProtonLaunchPlan stand-in for the pure-logic helpers."""
    return SimpleNamespace(
        prefix_path=prefix_root,
        state=SimpleNamespace(proton_tool_id=tool),
        context=SimpleNamespace(game_key="gog:123"),
    )


def _make_root_prefix(root: Path, *, proton_marker: str | None = None) -> None:
    """Build a root-layout Wine prefix with a save + setup markers."""
    (root / "drive_c" / "users" / "steamuser").mkdir(parents=True)
    (root / "drive_c" / "users" / "steamuser" / "save.dat").write_text("savegame")
    (root / "system.reg").write_text("reg")
    (root / "user.reg").write_text("reg")
    (root / "version").write_text("GE-Proton10-10")
    (root / "unifideck_winetricks_complete.marker").write_text("complete")
    (root / ".unifideck_prereqs_x.done").write_text("done")
    if proton_marker is not None:
        (root / pi._MARKER_NAME).write_text(proton_marker)


# ── _proton_family ────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("tool", "family"),
    [
        ("proton_experimental", "experimental"),
        ("GE-Proton10-34", "ge-proton"),
        ("GE-Proton9-26", "ge-proton"),
        ("UMU-Proton-9.0-4e", "umu-proton"),
        ("Proton 9.0 (Beta)", "proton9"),
        ("Proton 10.0", "proton10"),
        ("something-weird", "other"),
    ],
)
def test_proton_family(tool, family):
    assert pi._proton_family(tool) == family


# ── _handle_proton_change ─────────────────────────────────────────

def test_first_launch_records_marker_no_toast_no_reset(tmp_path, toast_spy):
    root = tmp_path / "prefix"
    _make_root_prefix(root, proton_marker=None)  # no marker → fresh baseline

    pi._handle_proton_change(_plan(root, "GE-Proton10-34"), root, "GE-Proton10-34")

    toast_spy.assert_not_called()
    assert (root / pi._MARKER_NAME).read_text() == "GE-Proton10-34"
    # Prefix untouched.
    assert (root / "system.reg").is_file()
    assert (root / "unifideck_winetricks_complete.marker").is_file()


def test_minor_change_notifies_but_keeps_prefix(tmp_path, toast_spy):
    root = tmp_path / "prefix"
    _make_root_prefix(root, proton_marker="GE-Proton10-10")

    pi._handle_proton_change(_plan(root, "GE-Proton10-34"), root, "GE-Proton10-34")

    key = toast_spy.call_args.args[0]
    assert key == "toasts.launcher.protonSwitchedTo"
    # Same family → prefix + setup markers preserved.
    assert (root / "system.reg").is_file()
    assert (root / "unifideck_winetricks_complete.marker").is_file()
    assert (root / pi._MARKER_NAME).read_text() == "GE-Proton10-34"


def test_major_change_resets_prefix_and_backs_up(tmp_path, toast_spy):
    root = tmp_path / "prefix"
    _make_root_prefix(root, proton_marker="proton_experimental")

    pi._handle_proton_change(_plan(root, "GE-Proton10-34"), root, "GE-Proton10-34")

    assert toast_spy.call_args.args[0] == "toasts.launcher.resettingPrefix"
    # Wine state + setup markers wiped...
    assert not (root / "system.reg").exists()
    assert not (root / "drive_c").exists()
    assert not (root / "unifideck_winetricks_complete.marker").exists()
    assert not (root / ".unifideck_prereqs_x.done").exists()
    # ...the proton marker is updated and the save is backed up.
    assert (root / pi._MARKER_NAME).read_text() == "GE-Proton10-34"
    backup = root / ".save_backup" / "steamuser" / "save.dat"
    assert backup.is_file()
    assert backup.read_text() == "savegame"


def test_reset_preserves_marker_and_backup_dirs(tmp_path):
    root = tmp_path / "prefix"
    _make_root_prefix(root, proton_marker="proton_experimental")
    (root / ".save_backup").mkdir()
    (root / ".save_backup" / "old").write_text("x")

    pi._reset_prefix(root)

    # A pre-existing backup is refreshed (old content gone) but the
    # backup + proton marker dirs/files themselves are never deleted.
    assert (root / pi._MARKER_NAME).is_file()
    assert (root / ".save_backup").is_dir()


# ── _ensure_created (fast path) ───────────────────────────────────

async def test_ensure_created_skips_when_system_reg_present(tmp_path, toast_spy):
    root = tmp_path / "prefix"
    root.mkdir()
    (root / "system.reg").write_text("reg")

    # Should return immediately without toasting or spawning umu.
    await pi._ensure_created(_plan(root, "GE-Proton10-34"), root)
    toast_spy.assert_not_called()


async def test_ensure_created_no_migration_when_already_initialised(
    tmp_path, monkeypatch,
):
    """An already-initialised prefix never triggers a legacy migration."""
    root = tmp_path / "prefix"
    root.mkdir()
    (root / "system.reg").write_text("reg")
    spy = MagicMock()
    monkeypatch.setattr(pi, "_restore_or_migrate_saves", spy)

    await pi._ensure_created(_plan(root, "GE-Proton10-34"), root)

    spy.assert_not_called()


async def test_ensure_created_skips_when_system_reg_under_pfx(tmp_path, toast_spy):
    """Regression: umu/Proton nest the real registry under ``pfx/``.

    WINEPREFIX is the prefix root, but umu-run creates the actual Wine
    tree at ``<root>/pfx/``. Checking ``root/system.reg`` directly never
    finds it, so a fully-initialised prefix looked "missing" on every
    single first launch — 3 pointless createprefix retries (each wiping
    the shared Steam Runtime cache) + a "Network Error" toast + a
    wineboot fallback, all failing the same way, before the game
    launched anyway.
    """
    root = tmp_path / "prefix"
    (root / "pfx").mkdir(parents=True)
    (root / "pfx" / "system.reg").write_text("reg")
    (root / "pfx" / "user.reg").write_text("reg")

    await pi._ensure_created(_plan(root, "GE-Proton10-34"), root)

    toast_spy.assert_not_called()


async def test_run_createprefix_with_retry_detects_success_under_pfx(
    tmp_path, toast_spy, monkeypatch,
):
    """A real createprefix success (registry lands under pfx/) must not retry."""
    root = tmp_path / "prefix"
    root.mkdir()

    async def _fake_run_umu(plan, env, *args):
        (root / "pfx").mkdir(parents=True, exist_ok=True)
        (root / "pfx" / "system.reg").write_text("reg")

    monkeypatch.setattr(pi, "_run_umu", _fake_run_umu)
    cleanup = MagicMock()
    monkeypatch.setattr(pi, "cleanup_umu_runtime_cache", cleanup)

    ok = await pi._run_createprefix_with_retry(
        _plan(root, "GE-Proton10-34"), {}, root,
    )

    assert ok is True
    cleanup.assert_not_called()  # no retry needed → cache never wiped
    assert not any(
        c.args[0] == "toasts.launcher.retryingUmu" for c in toast_spy.call_args_list
    )


# ── save migration / restore ──────────────────────────────────────


def _plan_with_env(prefix_root: Path, gameid: str | None = None):
    plan = _plan(prefix_root, "GE-Proton10-34")
    plan.env = {"GAMEID": gameid} if gameid else {}
    return plan


def _users_dir_with(root: Path, *, name: str, content: str) -> Path:
    """Build ``root/drive_c/users/steamuser/<name>`` and a user.reg."""
    users = root / "drive_c" / "users" / "steamuser"
    users.mkdir(parents=True, exist_ok=True)
    (root / "user.reg").write_text("reg")
    (users / name).write_text(content)
    return root / "drive_c" / "users"


def test_merge_users_copies_missing(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "steamuser").mkdir(parents=True)
    (src / "steamuser" / "save.dat").write_text("save")

    copied = pi._merge_users(src, dst)

    assert copied == 1
    assert (dst / "steamuser" / "save.dat").read_text() == "save"


def test_merge_users_skips_older_keeps_newer(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "steamuser").mkdir(parents=True)
    (dst / "steamuser").mkdir(parents=True)
    src_file = src / "steamuser" / "save.dat"
    dst_file = dst / "steamuser" / "save.dat"
    src_file.write_text("OLD")
    dst_file.write_text("NEW")
    # Destination is strictly newer than the source.
    import os
    os.utime(src_file, (1000, 1000))
    os.utime(dst_file, (2000, 2000))

    copied = pi._merge_users(src, dst)

    assert copied == 0
    assert dst_file.read_text() == "NEW"  # newer save not clobbered


def test_restore_save_backup_merges_into_users(tmp_path):
    root = tmp_path / "prefix"
    # Live (recreated) prefix has an empty users tree.
    _users_dir_with(root, name=".keep", content="")
    # Backup from a prior reset holds the real save.
    backup = root / ".save_backup" / "steamuser"
    backup.mkdir(parents=True)
    (backup / "save.dat").write_text("savegame")

    pi._restore_save_backup(root)

    restored = root / "drive_c" / "users" / "steamuser" / "save.dat"
    assert restored.read_text() == "savegame"


def test_migrate_legacy_prefix_copies_and_marks(tmp_path, monkeypatch):
    legacy_base = tmp_path / "Games" / "umu"
    monkeypatch.setattr(pi, "_LEGACY_UMU_BASE", str(legacy_base))
    # Legacy shared prefix with a save.
    _users_dir_with(legacy_base / "umu-0", name="save.dat", content="oldsave")
    # Fresh per-game prefix (created but empty users).
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")

    pi._migrate_legacy_prefix(_plan_with_env(root), root)

    migrated = root / "drive_c" / "users" / "steamuser" / "save.dat"
    assert migrated.read_text() == "oldsave"
    assert (root / pi._LEGACY_MIGRATED_MARKER).is_file()


def test_migrate_legacy_prefix_is_idempotent(tmp_path, monkeypatch):
    legacy_base = tmp_path / "Games" / "umu"
    monkeypatch.setattr(pi, "_LEGACY_UMU_BASE", str(legacy_base))
    _users_dir_with(legacy_base / "umu-0", name="save.dat", content="oldsave")
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")
    (root / pi._LEGACY_MIGRATED_MARKER).write_text("done")

    # Marker present → no copy attempted.
    pi._migrate_legacy_prefix(_plan_with_env(root), root)

    assert not (root / "drive_c" / "users" / "steamuser" / "save.dat").exists()


def test_migrate_legacy_prefix_marks_done_when_nothing_found(tmp_path, monkeypatch):
    legacy_base = tmp_path / "Games" / "umu"
    monkeypatch.setattr(pi, "_LEGACY_UMU_BASE", str(legacy_base))
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")

    pi._migrate_legacy_prefix(_plan_with_env(root), root)

    # No legacy data, but the marker is written so we don't rescan.
    assert (root / pi._LEGACY_MIGRATED_MARKER).is_file()


async def test_restore_or_migrate_prefers_save_backup(tmp_path, monkeypatch):
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")
    (root / ".save_backup").mkdir()
    restore = MagicMock()
    migrate = MagicMock()
    monkeypatch.setattr(pi, "_restore_save_backup", restore)
    monkeypatch.setattr(pi, "_migrate_legacy_prefix", migrate)

    await pi._restore_or_migrate_saves(_plan_with_env(root), root)

    restore.assert_called_once()
    migrate.assert_not_called()


async def test_restore_or_migrate_falls_back_to_legacy(tmp_path, monkeypatch):
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")
    restore = MagicMock()
    migrate = MagicMock()
    monkeypatch.setattr(pi, "_restore_save_backup", restore)
    monkeypatch.setattr(pi, "_migrate_legacy_prefix", migrate)

    await pi._restore_or_migrate_saves(_plan_with_env(root), root)

    migrate.assert_called_once()
    restore.assert_not_called()
