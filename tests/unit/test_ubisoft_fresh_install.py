"""Install always starts from a clean prefix; abandoned installs are removed.

Requirement: clicking Install deletes any pre-existing per-game prefix (prior
location + target) so no orphaned prefixes accumulate, and a failed/abandoned
install deletes its prefix unless it actually holds a game. Resume is
intentionally not preserved (an upc.exe-only prefix is no longer kept).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.stores.ubisoft.installer import registry as reg_mod
from unifideck.stores.ubisoft.installer.installer import UbisoftInstaller


def _installer() -> UbisoftInstaller:
    inst = UbisoftInstaller.__new__(UbisoftInstaller)
    inst._uninstall_pipeline = MagicMock()
    inst._uninstall_pipeline.delete_tree_with_retries = AsyncMock(return_value=True)
    inst._id_map = MagicMock()
    inst._paths = MagicMock()
    inst._library = MagicMock()
    return inst


def _deleted_paths(inst) -> list[str]:
    return [
        c.args[0]
        for c in inst._uninstall_pipeline.delete_tree_with_retries.call_args_list
    ]


# ── _reset_prefix_for_fresh_install ───────────────────────────────────


@pytest.mark.asyncio
async def test_reset_deletes_both_old_and_new_locations(tmp_path: Path):
    inst = _installer()
    old = tmp_path / "sd" / "prefixes" / "ubisoft" / "42"
    new = tmp_path / "internal" / "prefixes" / "ubisoft" / "42"
    old.mkdir(parents=True)
    new.mkdir(parents=True)

    await inst._reset_prefix_for_fresh_install(str(old), str(new))

    deleted = _deleted_paths(inst)
    assert str(old) in deleted and str(new) in deleted


@pytest.mark.asyncio
async def test_reset_dedupes_and_skips_missing(tmp_path: Path):
    inst = _installer()
    new = tmp_path / "prefixes" / "ubisoft" / "42"
    new.mkdir(parents=True)

    # old == new (same location re-install) and old is also None-safe
    await inst._reset_prefix_for_fresh_install(str(new), str(new))
    assert _deleted_paths(inst) == [str(new)]  # deduped to one delete

    inst._uninstall_pipeline.delete_tree_with_retries.reset_mock()
    await inst._reset_prefix_for_fresh_install(None, str(tmp_path / "nope"))
    inst._uninstall_pipeline.delete_tree_with_retries.assert_not_awaited()  # nothing exists


# ── _cleanup_abandoned_prefix ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_deletes_upc_only_prefix(tmp_path: Path, monkeypatch):
    """upc.exe present but NO game → delete (resume no longer preserved)."""
    inst = _installer()
    inst._id_map.resolve_prefix_path.return_value = str(tmp_path / "pfx")
    inst._library._detector._detect_installed_game.return_value = None
    inst._paths.find_upc_exe.return_value = tmp_path / "upc.exe"  # would've kept before
    monkeypatch.setattr(reg_mod, "prefix_has_game_files", lambda _p: False)

    await inst._cleanup_abandoned_prefix("42", str(tmp_path / "pfx"))

    inst._uninstall_pipeline.delete_tree_with_retries.assert_awaited_once()
    inst._id_map.clear_prefix_path.assert_called_once_with("42")


@pytest.mark.asyncio
async def test_cleanup_keeps_prefix_with_game_files(tmp_path: Path, monkeypatch):
    """A real game present → keep the prefix (never delete game files)."""
    inst = _installer()
    inst._id_map.resolve_prefix_path.return_value = str(tmp_path / "pfx")
    inst._library._detector._detect_installed_game.return_value = None
    monkeypatch.setattr(reg_mod, "prefix_has_game_files", lambda _p: True)

    await inst._cleanup_abandoned_prefix("42", str(tmp_path / "pfx"))

    inst._uninstall_pipeline.delete_tree_with_retries.assert_not_awaited()
