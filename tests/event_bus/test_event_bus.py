"""Tests for event_bus/event_bus.py (OP-09a)."""
from __future__ import annotations

import asyncio

import pytest

from unifideck.core.types import Events
from unifideck.event_bus.event_bus import EventBus


@pytest.mark.asyncio
async def test_emit_no_handlers():
    bus = EventBus()
    results = await bus.emit(Events.SYNC_STARTED)
    assert results == []


@pytest.mark.asyncio
async def test_on_and_emit():
    bus = EventBus()
    received = []

    async def handler(**kw):
        received.append(kw)

    bus.on(Events.GAME_INSTALLED, handler)
    await bus.emit(Events.GAME_INSTALLED, title="Hades", store="epic")
    assert len(received) == 1
    assert received[0]["title"] == "Hades"


@pytest.mark.asyncio
async def test_off_removes_handler():
    bus = EventBus()
    calls = []

    async def h(**kw):
        calls.append(1)

    bus.on(Events.SYNC_COMPLETE, h)
    assert bus.off(Events.SYNC_COMPLETE, h) is True
    assert bus.off(Events.SYNC_COMPLETE, h) is False  # already removed
    await bus.emit(Events.SYNC_COMPLETE)
    assert len(calls) == 0


@pytest.mark.asyncio
async def test_once_auto_removes():
    bus = EventBus()
    calls = []

    async def h(**kw):
        calls.append(1)

    bus.once(Events.STORE_AUTH_COMPLETE, h)
    assert bus.handler_count(Events.STORE_AUTH_COMPLETE) == 1
    await bus.emit(Events.STORE_AUTH_COMPLETE, store="gog")
    assert bus.handler_count(Events.STORE_AUTH_COMPLETE) == 0
    assert len(calls) == 1

    # Second emit should not call handler
    await bus.emit(Events.STORE_AUTH_COMPLETE, store="gog")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_error_isolation():
    """One failing handler must not block others."""
    bus = EventBus()
    ok_calls = []

    async def good(**kw):
        ok_calls.append("ok")

    async def bad(**kw):
        raise ValueError("boom")

    bus.on("test_event", good)
    bus.on("test_event", bad)
    results = await bus.emit("test_event")
    assert ok_calls == ["ok"]
    assert isinstance(results[1], ValueError)


@pytest.mark.asyncio
async def test_sync_handler_offloaded():
    """Sync handlers should run via to_thread."""
    bus = EventBus()
    result = []

    def sync_handler(**kw):
        result.append("sync")
        return 42

    bus.on("sync_test", sync_handler)
    r = await bus.emit("sync_test")
    assert result == ["sync"]
    assert r[0] == 42


@pytest.mark.asyncio
async def test_string_keys_survive_reload():
    """Events registered by string should be emittable by string."""
    bus = EventBus()
    calls = []

    async def h(**kw):
        calls.append(1)

    bus.on("game_installed", h)
    await bus.emit("game_installed", title="Test")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_clear_all():
    bus = EventBus()

    async def h(**kw):
        pass

    bus.on(Events.SYNC_STARTED, h)
    bus.on(Events.SYNC_COMPLETE, h)
    bus.clear()
    assert bus.handler_count(Events.SYNC_STARTED) == 0
    assert bus.handler_count(Events.SYNC_COMPLETE) == 0


@pytest.mark.asyncio
async def test_clear_specific_event():
    bus = EventBus()

    async def h(**kw):
        pass

    bus.on(Events.SYNC_STARTED, h)
    bus.on(Events.SYNC_COMPLETE, h)
    bus.clear(Events.SYNC_STARTED)
    assert bus.handler_count(Events.SYNC_STARTED) == 0
    assert bus.handler_count(Events.SYNC_COMPLETE) == 1
