"""GOGLibrary availability gate + fetch token-refresh (UD-005).

Regression guards for "GOG authenticates but the library shows 0
games". Two independent silent-zero vectors:

1. ``is_available()`` required a live HTTP 200 from ``userData.json``
   within a hard 5s timeout. A transient network/timeout blip made
   ``_probe_userdata`` return ``0`` and the store was dropped from the
   sync entirely (``registry.available()`` filters on the cached flag),
   so ``fetch_library`` never ran — 0 games, no error toast. A ``0``
   probe result (network/timeout) is now treated as "assume available"
   because we still hold tokens; a *real* 401/403 still marks the store
   unavailable.

2. ``fetch_library`` did not refresh a stale in-memory token before the
   first page GET, so a just-expired token → 401 → empty library. It
   now mirrors ``get_game_slug`` and calls ``refresh_if_stale`` first.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from unifideck.core.types import Game
from unifideck.stores.gog.config import GOGConfig
from unifideck.stores.gog.library import GOGLibrary


def _make_library(tokens: AsyncMock) -> GOGLibrary:
    """A GOGLibrary with a real (https) config and a mocked token mgr."""
    config = GOGConfig(
        base_url="https://embed.gog.com",
        api_gog_url="https://api.gog.com",
    )
    return GOGLibrary(config=config, tokens=tokens)


def _make_tokens(*, access: str | None = "live-token") -> AsyncMock:
    tokens = AsyncMock()
    # Properties on the real manager — set as plain attributes so reads
    # don't return coroutines.
    tokens.access_token = access
    tokens.has_tokens = access is not None
    return tokens


# ── Fix 1: stale-token refresh before the fetch loop ──────────────


async def test_fetch_library_refreshes_stale_token_before_fetch():
    """The core UD-005 guard: refresh runs, and BEFORE any page GET."""
    tokens = _make_tokens()
    tokens.refresh_if_stale.return_value = True
    lib = _make_library(tokens)

    order: list[str] = []
    tokens.refresh_if_stale.side_effect = lambda: order.append("refresh") or True

    async def _fake_fetch_json(url, headers=None):
        order.append("fetch")
        return {
            "totalPages": 1,
            "totalGamesFound": 1,
            "products": [{"id": 42, "title": "Test Game"}],
        }

    lib._fetch_json = _fake_fetch_json  # type: ignore[method-assign]

    games = await lib.fetch_library()

    assert [g.store_game_id for g in games] == ["42"]
    assert isinstance(games[0], Game)
    # refresh must have happened, and before the first fetch.
    assert order[0] == "refresh"
    assert "fetch" in order
    assert order.index("refresh") < order.index("fetch")


async def test_fetch_library_aborts_when_refresh_fails():
    """No valid token → return [] and never hit the network."""
    tokens = _make_tokens(access=None)
    tokens.refresh_if_stale.return_value = False
    lib = _make_library(tokens)

    fetch = AsyncMock()
    lib._fetch_json = fetch  # type: ignore[method-assign]

    games = await lib.fetch_library()

    assert games == []
    fetch.assert_not_called()


# ── Fix 2: availability robust to a transient probe blip ──────────


async def test_is_available_true_on_probe_timeout_with_tokens():
    """status 0 (network/timeout) must NOT drop the store from sync."""
    tokens = _make_tokens()
    lib = _make_library(tokens)
    lib._probe_userdata = AsyncMock(return_value=0)  # type: ignore[method-assign]

    assert await lib.is_available() is True


async def test_is_available_true_on_probe_200():
    """Happy path is unchanged."""
    tokens = _make_tokens()
    lib = _make_library(tokens)
    lib._probe_userdata = AsyncMock(return_value=200)  # type: ignore[method-assign]

    assert await lib.is_available() is True


async def test_is_available_false_on_real_403():
    """A real non-0/non-200/non-401 status stays unavailable — the
    'assume available' relaxation is pinned to status 0 only."""
    tokens = _make_tokens()
    lib = _make_library(tokens)
    lib._probe_userdata = AsyncMock(return_value=403)  # type: ignore[method-assign]

    assert await lib.is_available() is False


async def test_is_available_false_on_401_when_refresh_fails():
    """A genuine expired-and-unrefreshable token clears creds and
    reports unavailable (existing behaviour preserved)."""
    tokens = _make_tokens()
    tokens.refresh_if_stale.return_value = False
    lib = _make_library(tokens)
    # First probe 401; refresh fails, so no second probe happens.
    lib._probe_userdata = AsyncMock(return_value=401)  # type: ignore[method-assign]

    assert await lib.is_available() is False
    tokens.clear.assert_awaited_once()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
