"""Regression tests for the frontend → bus launch bridge.

These guard the playtime/achievements/cloud-save recording pipeline at its
single point of failure: ``LaunchRPCMixin._resolve_app_id``. The frontend
lifetime listener only knows the Steam AppID, so the mixin resolves
``(store, game_id, title)`` from the sync service before emitting
``GAME_LAUNCHED`` / ``GAME_STOPPED`` on the plugin bus.

The bug this catches: the resolver read ``info["id"]`` / ``info["game_id"]``,
but ``SyncService.get_game_info`` returns ``asdict(Game)`` whose store-native
id key is ``store_game_id`` (there is no ``id``/``game_id`` field). So
``resolved_game`` was always ``None`` and *every* launch/stop silently
returned ``skipped: not_unifideck_app`` — no event fired and the playtime DB
stayed empty. The previous suite only exercised the explicit
``notify_game_launched(store=…, game_id=…)`` signature, which bypasses
``_resolve_app_id``'s AppID branch entirely, so the outage passed CI.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from unifideck.core.types.domain import Game
from unifideck.core.types.events import Events
from unifideck.rpc.mixins.launch import LaunchRPCMixin


class _FakeBus:
    """Records every ``emit`` so tests can assert what reached the bus."""

    def __init__(self) -> None:
        self.emitted: list[tuple[Any, dict[str, Any]]] = []

    async def emit(self, event: Any, **kwargs: Any) -> None:
        self.emitted.append((event, kwargs))


class _FakeSync:
    """Stands in for ``SyncService`` — returns ``asdict(Game)`` on a hit.

    This mirrors the real ``get_game_info`` contract exactly: a matching
    AppID yields the dataclass dict (keyed by ``store_game_id``), a miss
    yields ``None``.
    """

    def __init__(self, game: Game | None) -> None:
        self._game = game

    def get_game_info(self, app_id: int) -> dict[str, Any] | None:
        return asdict(self._game) if self._game is not None else None


class _Host(LaunchRPCMixin):
    """Minimal mixin host with just the attributes the mixin touches."""

    def __init__(self, bus: _FakeBus, sync: _FakeSync) -> None:
        self.bus = bus
        self.sync_service = sync
        self.services = None
        self.config = None


def _make_game() -> Game:
    return Game(
        app_id=2147483647,
        store="gog",
        store_game_id="12345",
        title="Stardew Valley",
    )


def test_resolve_app_id_reads_store_game_id() -> None:
    """``_resolve_app_id`` must read ``store_game_id`` (the real key)."""
    game = _make_game()
    host = _Host(_FakeBus(), _FakeSync(game))

    store, game_id, title = host._resolve_app_id(game.app_id, None, None)

    assert store == "gog"
    # Regression: this was ``None`` because the resolver read ``id``/``game_id``.
    assert game_id == "12345"
    assert title == "Stardew Valley"


def test_notify_game_launched_emits_for_known_shortcut() -> None:
    game = _make_game()
    bus = _FakeBus()
    host = _Host(bus, _FakeSync(game))

    res = asyncio.run(host.notify_game_launched(game.app_id))

    assert res == {"success": True}
    assert "skipped" not in res
    assert len(bus.emitted) == 1
    event, kwargs = bus.emitted[0]
    assert event is Events.GAME_LAUNCHED
    assert kwargs["store"] == "gog"
    assert kwargs["game_id"] == "12345"
    # Title is threaded through so PlaytimeService records a real name.
    assert kwargs["title"] == "Stardew Valley"


def test_notify_game_stopped_emits_for_known_shortcut() -> None:
    game = _make_game()
    bus = _FakeBus()
    host = _Host(bus, _FakeSync(game))

    res = asyncio.run(host.notify_game_stopped(game.app_id, exit_code=0))

    assert res == {"success": True}
    assert len(bus.emitted) == 1
    event, kwargs = bus.emitted[0]
    assert event is Events.GAME_STOPPED
    assert kwargs["store"] == "gog"
    assert kwargs["game_id"] == "12345"


def test_notify_skips_unknown_app() -> None:
    """A non-Unifideck AppID is a quiet no-op — no event emitted."""
    bus = _FakeBus()
    host = _Host(bus, _FakeSync(None))

    res = asyncio.run(host.notify_game_launched(999999))

    assert res == {"success": True, "skipped": "not_unifideck_app"}
    assert bus.emitted == []


def test_explicit_store_game_id_still_works() -> None:
    """The explicit ``(store, game_id)`` signature bypasses resolution."""
    bus = _FakeBus()
    host = _Host(bus, _FakeSync(None))

    res = asyncio.run(
        host.notify_game_launched(None, store="epic", game_id="abc")
    )

    assert res == {"success": True}
    event, kwargs = bus.emitted[0]
    assert event is Events.GAME_LAUNCHED
    assert kwargs["store"] == "epic"
    assert kwargs["game_id"] == "abc"
