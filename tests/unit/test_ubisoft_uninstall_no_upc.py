"""Ubisoft uninstall must not run UPC when it can delete the files itself.

Running UPC's ``uplay://uninstall`` rotates the Ubisoft refresh token and
logs the shared session out, so the next install opens signed-out ("auth
lost after uninstall"). The pipeline now prefers deleting the located
install directory directly and only falls back to the UPC protocol when the
files can't be located — capturing the rotated token back in that case so
even the fallback keeps the shared login.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.stores.ubisoft.installer.uninstall import _UninstallPipeline


def _pipeline(prefix_path: str, install_id: str | None, install_path: str | None):
    pipe = _UninstallPipeline(MagicMock())
    pipe.resolve_uninstall_targets = MagicMock(
        return_value=(prefix_path, install_id, install_path),
    )
    pipe.attempt_protocol_uninstall = AsyncMock(return_value=True)
    pipe._capture_rotated_session = MagicMock()
    pipe.refresh_install_path = MagicMock(side_effect=lambda _g, _p, i: i)
    pipe.delete_game_directory = AsyncMock(return_value=None)
    pipe.delete_prefix_if_requested = AsyncMock(return_value=(False, None))
    pipe.post_uninstall_cleanup = MagicMock()
    return pipe


@pytest.mark.asyncio
async def test_uninstall_skips_upc_when_files_located(tmp_path: Path) -> None:
    """A concrete install dir on disk → delete directly, never touch UPC.

    Even on the no-UPC path the prefix's current token is captured back to
    auth BEFORE deletion (the prefix can hold a newer play-rotated token than
    auth), so a subsequent install isn't stranded on a stale token.
    """
    game_dir = tmp_path / "Beyond Good and Evil"
    game_dir.mkdir()
    pipe = _pipeline(str(tmp_path / "prefix"), "1234", str(game_dir))

    result = await pipe.uninstall_game("0f33986d", delete_prefix=False)

    assert result.success
    pipe.attempt_protocol_uninstall.assert_not_awaited()  # no UPC → token untouched
    pipe._capture_rotated_session.assert_called_once()     # captured before delete
    pipe.delete_game_directory.assert_awaited_once()       # we removed the files


@pytest.mark.asyncio
async def test_uninstall_falls_back_to_upc_when_files_missing(
    tmp_path: Path,
) -> None:
    """No locatable install dir → fall back to UPC, and capture-back auth."""
    pipe = _pipeline(str(tmp_path / "prefix"), "1234", None)

    result = await pipe.uninstall_game("0f33986d", delete_prefix=False)

    assert result.success
    pipe.attempt_protocol_uninstall.assert_awaited_once()  # UPC fallback ran
    pipe._capture_rotated_session.assert_called_once()      # rotated token preserved
