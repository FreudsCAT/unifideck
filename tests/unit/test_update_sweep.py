"""The background sweep: cache warming, change-only announcements, teardown.

Reported as "the Update button takes 5-10 s to show up — what if the user
starts the game first?". The scan itself is inherently slow (``legendary
list-installed --check-updates`` logs in to Epic and re-downloads the
asset manifest before printing), so the fix is not to make it faster but
to stop running it on the interaction path.

``UpdateSweepService`` runs it in the background instead — shortly after
boot, again after every library sync, then every 6 h — and writes the
answer into the cache the RPC reads. These tests pin the behaviours that
make that safe:

* the result reaches the cache, so ``check_game_update`` can answer
  without blocking;
* ``GAME_UPDATE_AVAILABLE`` fires on CHANGE only — announcing every
  sweep would re-nag about an update the user has already declined,
  and the disappearance case (update applied) must announce too;
* one bad store cannot fail the sweep for the others;
* ``stop()`` detaches from the bus, so a Decky reload doesn't leave a
  dead instance scanning.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.core.types import Events
from unifideck.services import update_check_cache
from unifideck.services.update_sweep import UpdateSweepService


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    """One test's scan must never answer another's question."""
    update_check_cache.clear()


class _FakeBus:
    """Records emissions and on/off calls without a real event loop."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event: Any, handler: Any) -> None:
        self.handlers.setdefault(str(getattr(event, "value", event)), []).append(handler)

    def off(self, event: Any, handler: Any) -> bool:
        key = str(getattr(event, "value", event))
        if handler in self.handlers.get(key, []):
            self.handlers[key].remove(handler)
            return True
        return False

    async def emit(self, event: Any, **payload: Any) -> list[Any]:
        self.emitted.append((str(getattr(event, "value", event)), payload))
        return []


def _registry(stores: dict[str, Any]) -> Any:
    reg = MagicMock()
    reg.store_ids.return_value = list(stores)
    reg.get_store.side_effect = stores.get
    return reg


def _store(ids: list[str]) -> Any:
    store = MagicMock()
    store.check_for_updates = AsyncMock(return_value=ids)
    return store


@pytest.mark.asyncio
async def test_sweep_warms_the_cache_the_rpc_reads() -> None:
    """The whole point: after a sweep, answering is a memory read."""
    bus = _FakeBus()
    svc = UpdateSweepService(bus, _registry({"epic": _store(["Sugar"])}))

    assert await svc.sweep() == {"epic": ["Sugar"]}
    assert update_check_cache.peek("epic") == ["Sugar"]


@pytest.mark.asyncio
async def test_announces_the_first_result() -> None:
    bus = _FakeBus()
    svc = UpdateSweepService(bus, _registry({"epic": _store(["Sugar"])}))

    await svc.sweep()

    assert bus.emitted == [
        (Events.GAME_UPDATE_AVAILABLE.value, {"store": "epic", "game_ids": ["Sugar"]}),
    ]


@pytest.mark.asyncio
async def test_an_unchanged_sweep_stays_silent() -> None:
    """Re-announcing every 6 h would nag about a declined update."""
    bus = _FakeBus()
    epic = _store(["Sugar"])
    svc = UpdateSweepService(bus, _registry({"epic": epic}))

    await svc.sweep()
    # ttl=0 on the second call would be coalesced away; go around the
    # window so the store is genuinely re-scanned.
    svc.COALESCE_WINDOW_S = 0
    update_check_cache.clear()
    await svc.sweep()

    assert epic.check_for_updates.await_count == 2
    assert len(bus.emitted) == 1


@pytest.mark.asyncio
async def test_announces_when_an_update_disappears() -> None:
    """Applying the update must clear the button, not wait 6 h."""
    bus = _FakeBus()
    epic = _store(["Sugar"])
    svc = UpdateSweepService(bus, _registry({"epic": epic}))
    svc.COALESCE_WINDOW_S = 0

    await svc.sweep()
    epic.check_for_updates = AsyncMock(return_value=[])
    update_check_cache.clear()
    await svc.sweep()

    assert [p for _, p in bus.emitted] == [
        {"store": "epic", "game_ids": ["Sugar"]},
        {"store": "epic", "game_ids": []},
    ]


@pytest.mark.asyncio
async def test_one_failing_store_does_not_sink_the_others() -> None:
    """A logged-out Epic must not hide GOG's pending updates."""
    bus = _FakeBus()
    broken = MagicMock()
    broken.check_for_updates = AsyncMock(side_effect=RuntimeError("legendary died"))
    svc = _sweep(bus, {"epic": broken, "gog": _store(["1549126051"])})

    result = await svc.sweep()

    assert result == {"epic": [], "gog": ["1549126051"]}


@pytest.mark.asyncio
async def test_unknown_store_id_is_not_an_error() -> None:
    bus = _FakeBus()
    reg = MagicMock()
    reg.store_ids.return_value = ["ghost"]
    reg.get_store.return_value = None
    svc = UpdateSweepService(bus, reg)

    assert await svc.sweep() == {"ghost": []}
    assert bus.emitted == []


@pytest.mark.asyncio
async def test_stop_detaches_from_the_bus() -> None:
    """A leaked handler would keep a dead instance scanning after reload."""
    bus = _FakeBus()
    svc = UpdateSweepService(bus, _registry({}))

    await svc.start()
    assert bus.handlers[Events.SYNC_COMPLETE.value]
    await svc.stop()

    assert bus.handlers[Events.SYNC_COMPLETE.value] == []


@pytest.mark.asyncio
async def test_sync_complete_triggers_a_refresh() -> None:
    """A sync can change what is installed — re-scan rather than wait 6 h."""
    bus = _FakeBus()
    epic = _store(["Sugar"])
    svc = UpdateSweepService(bus, _registry({"epic": epic}))
    await svc.start()
    try:
        handler = bus.handlers[Events.SYNC_COMPLETE.value][0]
        await handler()
        # request_refresh is fire-and-forget; let its task run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert update_check_cache.peek("epic") == ["Sugar"]
    finally:
        await svc.stop()


def _sweep(bus: Any, stores: dict[str, Any]) -> UpdateSweepService:
    """Build a service over a fake registry of ``stores``."""
    return UpdateSweepService(bus, _registry(stores))
