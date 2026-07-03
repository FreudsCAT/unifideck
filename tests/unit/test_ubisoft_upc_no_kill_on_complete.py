"""UPC must not be killed mid-install; only an explicit cancel closes it.

Completion is inferred from the install dir's size holding steady, which can
misfire during a mid-download pause — so killing UPC on the completion/timeout
path would interrupt a still-running download. Only a download-queue CANCEL
(``CancelledError``) closes UPC.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.core.types import InstallResult
from unifideck.stores.ubisoft.installer.manual_ui import _ManualUiInstaller


def _installer() -> _ManualUiInstaller:
    inst = _ManualUiInstaller.__new__(_ManualUiInstaller)
    inst._session = MagicMock()
    inst._active_install_pids = {}
    inst._pkill_upc = MagicMock()
    inst._capture_and_propagate_session = MagicMock()
    inst._snapshot_pre_install = MagicMock(return_value=("/base", set(), {}))
    inst._notify_upc_launching = AsyncMock()
    inst._finalize_manual_install = AsyncMock(
        return_value=InstallResult(success=True, store="ubisoft", game_id="80"),
    )
    return inst


async def _run(inst: _ManualUiInstaller):
    return await inst.install_via_upc_ui(
        game_id="80",
        game_name="Rayman Origins",
        prefix_path="/pfx",
        env={},
        progress_cb=None,
        install_path="/base",
        on_ready=None,
    )


@pytest.mark.asyncio
async def test_completion_does_not_kill_upc():
    inst = _installer()
    inst._poll_for_new_install = AsyncMock(return_value="/install/dir")

    result = await _run(inst)

    assert result.success
    inst._pkill_upc.assert_not_called()               # UPC left open on completion
    inst._finalize_manual_install.assert_awaited_once()
    inst._capture_and_propagate_session.assert_called_once()  # token still captured


@pytest.mark.asyncio
async def test_cancel_kills_upc():
    inst = _installer()
    inst._poll_for_new_install = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _run(inst)

    inst._pkill_upc.assert_called_once()              # explicit cancel closes UPC
    inst._capture_and_propagate_session.assert_called_once()
    inst._finalize_manual_install.assert_not_awaited()
