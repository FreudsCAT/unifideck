"""Regression: one wedged subscriber must not hang ``emit`` forever.

``SYNC_STARTED``/``SYNC_COMPLETE`` are emitted while ``SyncService`` holds
its single-flight ``_lock`` — a handler stuck on an uncancellable await
used to hang the whole ``emit`` call, wedging the lock forever (every
future sync request then queues behind it indefinitely; only a plugin
restart, which rebuilds ``SyncService`` with a fresh ``Lock()``, recovered).
``EventBus._invoke`` now bounds each handler with
``HANDLER_TIMEOUT_SECONDS`` and reports a timeout like any other
per-handler failure instead of hanging (UD-013).
"""
from __future__ import annotations

import asyncio

import pytest

from unifideck.event_bus import event_bus as m
from unifideck.event_bus.event_bus import EventBus


@pytest.mark.asyncio
async def test_wedged_handler_times_out_without_hanging_emit(monkeypatch):
    monkeypatch.setattr(m, "HANDLER_TIMEOUT_SECONDS", 0.1)
    bus = EventBus()

    async def _wedged(**_kwargs: object) -> None:
        await asyncio.sleep(10.0)

    bus.on("sync_started", _wedged)
    results = await asyncio.wait_for(bus.emit("sync_started"), timeout=1.0)
    assert len(results) == 1
    assert isinstance(results[0], TimeoutError)


@pytest.mark.asyncio
async def test_wedged_handler_does_not_block_sibling_handlers(monkeypatch):
    monkeypatch.setattr(m, "HANDLER_TIMEOUT_SECONDS", 0.1)
    bus = EventBus()
    sibling_ran = False

    async def _wedged(**_kwargs: object) -> None:
        await asyncio.sleep(10.0)

    async def _sibling(**_kwargs: object) -> None:
        nonlocal sibling_ran
        sibling_ran = True

    bus.on("sync_started", _wedged)
    bus.on("sync_started", _sibling)
    await asyncio.wait_for(bus.emit("sync_started"), timeout=1.0)
    assert sibling_ran is True


@pytest.mark.asyncio
async def test_fast_handler_result_is_unaffected(monkeypatch):
    monkeypatch.setattr(m, "HANDLER_TIMEOUT_SECONDS", 5)
    bus = EventBus()

    async def _fast(**_kwargs: object) -> str:
        return "ok"

    bus.on("sync_started", _fast)
    results = await bus.emit("sync_started")
    assert results == ["ok"]
