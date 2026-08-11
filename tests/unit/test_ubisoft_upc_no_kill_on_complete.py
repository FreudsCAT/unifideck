"""UPC must not be killed mid-install; only an explicit cancel closes it.

Completion is inferred from the install dir's size holding steady, which can
misfire during a mid-download pause — so killing UPC on the completion/timeout
path would interrupt a still-running download. Only a download-queue CANCEL
(``CancelledError``) closes UPC.

The watching loop is shared with every wrapper store now, so these drive the
real ``install_via_upc_ui`` with the shared watcher stubbed at its seam, and
assert on the shared, table-driven ``kill_client`` rather than a private
``pkill``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.core.types import InstallResult
from unifideck.stores.ubisoft.installer import manual_ui as mod
from unifideck.stores.ubisoft.installer.manual_ui import _ManualUiInstaller


def _installer() -> _ManualUiInstaller:
    inst = _ManualUiInstaller.__new__(_ManualUiInstaller)
    inst._session = MagicMock()
    inst._active_install_pids = {}
    inst._capture_and_propagate_session = MagicMock()
    inst._prepared_install_base = MagicMock(return_value="/base")
    inst._finalize_manual_install = AsyncMock(
        return_value=InstallResult(success=True, store="ubisoft", game_id="80"),
    )
    return inst


def _watcher(monkeypatch, **kwargs) -> MagicMock:
    """Stub the shared watcher and spy on the shared client stop."""
    monkeypatch.setattr(mod, "watch_manual_install", AsyncMock(**kwargs))
    killed = MagicMock()
    monkeypatch.setattr(mod, "kill_client", killed)
    return killed


async def _run(inst: _ManualUiInstaller):
    return await inst.install_via_upc_ui(
        game_id="80",
        game_name="Rayman Origins",
        prefix_path="/pfx",
        progress_cb=None,
        install_path="/base",
        on_ready=None,
    )


@pytest.mark.asyncio
async def test_completion_does_not_kill_upc(monkeypatch):
    inst = _installer()
    killed = _watcher(monkeypatch, return_value="/install/dir")

    result = await _run(inst)

    assert result.success
    killed.assert_not_called()                        # UPC left open on completion
    inst._finalize_manual_install.assert_awaited_once()
    inst._capture_and_propagate_session.assert_called_once()  # token still captured


@pytest.mark.asyncio
async def test_cancel_kills_upc(monkeypatch):
    inst = _installer()
    killed = _watcher(monkeypatch, side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _run(inst)

    killed.assert_called_once()                       # explicit cancel closes UPC
    assert killed.call_args.args[0] == "ubisoft"      # ...this store's client only
    inst._capture_and_propagate_session.assert_called_once()
    inst._finalize_manual_install.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_install_detected_leaves_upc_alone(monkeypatch):
    """A watch that ends without a game must not close a client still working.

    The abandonment watchdogs already require the client to be gone, so a kill
    here could only ever hit a live one — e.g. after the overall timeout.
    """
    inst = _installer()
    killed = _watcher(monkeypatch, return_value=None)

    result = await _run(inst)

    assert not result.success
    assert result.error == "no_install_detected"
    killed.assert_not_called()
    inst._capture_and_propagate_session.assert_called_once()
