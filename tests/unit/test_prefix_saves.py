"""Unit tests for compat/prefix_saves — save restore + legacy migration.

Split out of ``test_prefix_init`` alongside the module itself. Covers the
mtime-guarded merge, the ``.save_backup`` restore a Proton-family reset
depends on, and the one-time pull-forward from the pre-0.6 shared umu
prefix.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from unifideck.launcher.proton.compat import prefix_saves as ps


def _plan_with_env(prefix_root: Path, gameid: str | None = None):
    """Minimal ProtonLaunchPlan stand-in; only ``env`` is read here."""
    plan = SimpleNamespace(
        prefix_path=prefix_root,
        state=SimpleNamespace(proton_tool_id="GE-Proton10-34"),
        context=SimpleNamespace(game_key="gog:123"),
    )
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

    copied = ps._merge_users(src, dst)

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

    copied = ps._merge_users(src, dst)

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

    ps._restore_save_backup(root)

    restored = root / "drive_c" / "users" / "steamuser" / "save.dat"
    assert restored.read_text() == "savegame"


def test_migrate_legacy_prefix_copies_and_marks(tmp_path, monkeypatch):
    legacy_base = tmp_path / "Games" / "umu"
    monkeypatch.setattr(ps, "_LEGACY_UMU_BASE", str(legacy_base))
    # Legacy shared prefix with a save.
    _users_dir_with(legacy_base / "umu-0", name="save.dat", content="oldsave")
    # Fresh per-game prefix (created but empty users).
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")

    ps._migrate_legacy_prefix(_plan_with_env(root), root)

    migrated = root / "drive_c" / "users" / "steamuser" / "save.dat"
    assert migrated.read_text() == "oldsave"
    assert (root / ps._LEGACY_MIGRATED_MARKER).is_file()


def test_migrate_legacy_prefix_is_idempotent(tmp_path, monkeypatch):
    legacy_base = tmp_path / "Games" / "umu"
    monkeypatch.setattr(ps, "_LEGACY_UMU_BASE", str(legacy_base))
    _users_dir_with(legacy_base / "umu-0", name="save.dat", content="oldsave")
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")
    (root / ps._LEGACY_MIGRATED_MARKER).write_text("done")

    # Marker present → no copy attempted.
    ps._migrate_legacy_prefix(_plan_with_env(root), root)

    assert not (root / "drive_c" / "users" / "steamuser" / "save.dat").exists()


def test_migrate_legacy_prefix_marks_done_when_nothing_found(tmp_path, monkeypatch):
    legacy_base = tmp_path / "Games" / "umu"
    monkeypatch.setattr(ps, "_LEGACY_UMU_BASE", str(legacy_base))
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")

    ps._migrate_legacy_prefix(_plan_with_env(root), root)

    # No legacy data, but the marker is written so we don't rescan.
    assert (root / ps._LEGACY_MIGRATED_MARKER).is_file()


async def test_restore_or_migrate_prefers_save_backup(tmp_path, monkeypatch):
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")
    (root / ".save_backup").mkdir()
    restore = MagicMock()
    migrate = MagicMock()
    monkeypatch.setattr(ps, "_restore_save_backup", restore)
    monkeypatch.setattr(ps, "_migrate_legacy_prefix", migrate)

    await ps._restore_or_migrate_saves(_plan_with_env(root), root)

    restore.assert_called_once()
    migrate.assert_not_called()


async def test_restore_or_migrate_falls_back_to_legacy(tmp_path, monkeypatch):
    root = tmp_path / "prefix"
    _users_dir_with(root, name=".keep", content="")
    restore = MagicMock()
    migrate = MagicMock()
    monkeypatch.setattr(ps, "_restore_save_backup", restore)
    monkeypatch.setattr(ps, "_migrate_legacy_prefix", migrate)

    await ps._restore_or_migrate_saves(_plan_with_env(root), root)

    migrate.assert_called_once()
    restore.assert_not_called()
