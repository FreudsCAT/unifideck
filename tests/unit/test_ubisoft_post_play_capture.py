"""After a Ubisoft play session, the rotated UPC token is captured to auth.

The Play path runs in the launcher subprocess and can't capture back, so UPC's
in-play token rotation would otherwise strand the auth prefix on a stale token
(next fresh install opens signed-out). ``UbisoftStore`` subscribes to
``GAME_STOPPED`` and captures the game prefix's token to auth on stop.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from unifideck.stores.ubisoft.store import UbisoftStore


def _store() -> UbisoftStore:
    s = UbisoftStore.__new__(UbisoftStore)
    s._paths = MagicMock()
    s._paths.get_prefix_path.return_value = "/prefix/ubisoft/80"
    s._session = MagicMock()
    s._session.capture.return_value = "captured"  # truthy sentinel
    return s


@pytest.mark.asyncio
async def test_captures_ubisoft_session_on_stop():
    s = _store()

    await s._capture_upc_session_on_stop(store="ubisoft", game_id="80")

    s._session.capture.assert_called_once_with("/prefix/ubisoft/80")
    s._session.propagate_all_to_all.assert_called_once()


@pytest.mark.asyncio
async def test_ignores_non_ubisoft_stop():
    s = _store()

    await s._capture_upc_session_on_stop(store="gog", game_id="123")

    s._session.capture.assert_not_called()
    s._session.propagate_all_to_all.assert_not_called()


@pytest.mark.asyncio
async def test_noop_when_nothing_to_capture():
    """capture() returns None (logged-out / unchanged) → no propagation."""
    s = _store()
    s._session.capture.return_value = None

    await s._capture_upc_session_on_stop(store="ubisoft", game_id="80")

    s._session.propagate_all_to_all.assert_not_called()
