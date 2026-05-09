"""Tests for event_bus/supervision/watchdog_handler.py (OP-10a)."""
from __future__ import annotations

import asyncio

import pytest

from unifideck.event_bus.supervision.watchdog_handler import (
    HandlerQuarantinedError,
    HandlerWatchdog,
)


@pytest.mark.asyncio
async def test_successful_invoke_resets_counter():
    wd = HandlerWatchdog(default_timeout=1.0, quarantine_threshold=3)

    async def fast():
        return "ok"

    wd.register("fast")
    result = await wd.invoke(handler_name="fast", handler=fast)
    assert result == "ok"
    assert wd.get_metrics()["fast"].consecutive_timeouts == 0


@pytest.mark.asyncio
async def test_timeout_increments_counter():
    wd = HandlerWatchdog(default_timeout=0.05, quarantine_threshold=3)

    async def slow():
        await asyncio.sleep(10)

    wd.register("slow")
    with pytest.raises(asyncio.TimeoutError):
        await wd.invoke(handler_name="slow", handler=slow)
    assert wd.get_metrics()["slow"].timeouts == 1
    assert wd.get_metrics()["slow"].consecutive_timeouts == 1


@pytest.mark.asyncio
async def test_quarantine_after_threshold():
    wd = HandlerWatchdog(default_timeout=0.05, quarantine_threshold=2)

    async def slow():
        await asyncio.sleep(10)

    wd.register("slow")
    for _ in range(2):
        with pytest.raises(asyncio.TimeoutError):
            await wd.invoke(handler_name="slow", handler=slow)

    assert wd.get_metrics()["slow"].quarantined is True

    # Quarantined handler raises immediately
    async def fast():
        return "ok"

    with pytest.raises(HandlerQuarantinedError):
        await wd.invoke(handler_name="slow", handler=fast)


@pytest.mark.asyncio
async def test_release_quarantine():
    wd = HandlerWatchdog(default_timeout=0.05, quarantine_threshold=1)

    async def slow():
        await asyncio.sleep(10)

    wd.register("h")
    with pytest.raises(asyncio.TimeoutError):
        await wd.invoke(handler_name="h", handler=slow)

    assert wd.get_metrics()["h"].quarantined is True
    assert wd.release_quarantine("h") is True
    assert wd.get_metrics()["h"].quarantined is False
    assert wd.release_quarantine("h") is False  # already released


def test_preemptive_quarantine():
    wd = HandlerWatchdog()
    assert wd.quarantine_preemptive("bad_handler", "known bug") is True
    assert wd.get_metrics()["bad_handler"].quarantined is True
    assert wd.quarantine_preemptive("bad_handler") is False  # already quarantined
