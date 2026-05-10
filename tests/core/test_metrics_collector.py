"""Tests for core/metrics_collector.py (OP-04b)."""
from __future__ import annotations

import asyncio
import time

import pytest

from unifideck.core.types import Events
from unifideck.event_bus.event_bus import EventBus
from unifideck.core.metrics_collector import MetricsCollector


@pytest.mark.asyncio
async def test_counter_increments_on_event():
    bus = EventBus()
    mc = MetricsCollector(bus)
    await bus.emit(Events.GAME_INSTALLED, title="Hades")
    metrics = mc.get_plugin_metrics()
    assert metrics["counters"].get("game_installed", 0) >= 1


@pytest.mark.asyncio
async def test_timer_pair_auth():
    bus = EventBus()
    mc = MetricsCollector(bus)
    await bus.emit(Events.STORE_AUTH_STARTED, store="epic")
    await asyncio.sleep(0.05)
    await bus.emit(Events.STORE_AUTH_COMPLETE, store="epic")
    metrics = mc.get_plugin_metrics()
    assert "auth_duration_ms:epic" in metrics["timers_ms"]
    assert metrics["timers_ms"]["auth_duration_ms:epic"] > 0


@pytest.mark.asyncio
async def test_timer_pair_sync():
    bus = EventBus()
    mc = MetricsCollector(bus)
    await bus.emit(Events.SYNC_STARTED)
    await asyncio.sleep(0.05)
    await bus.emit(Events.SYNC_COMPLETE)
    metrics = mc.get_plugin_metrics()
    assert "sync_duration_ms" in metrics["timers_ms"]


@pytest.mark.asyncio
async def test_uptime():
    bus = EventBus()
    mc = MetricsCollector(bus)
    metrics = mc.get_plugin_metrics()
    assert "uptime_s" in metrics
    assert metrics["uptime_s"] >= 0


@pytest.mark.asyncio
async def test_reset():
    bus = EventBus()
    mc = MetricsCollector(bus)
    await bus.emit(Events.GAME_INSTALLED, title="test")
    mc.reset()
    metrics = mc.get_plugin_metrics()
    assert len(metrics["counters"]) == 0
    assert len(metrics["timers_ms"]) == 0


@pytest.mark.asyncio
async def test_gauge_from_sync_complete():
    bus = EventBus()
    mc = MetricsCollector(bus)
    await bus.emit(Events.SYNC_COMPLETE, games=[1, 2, 3], stores_synced=2)
    metrics = mc.get_plugin_metrics()
    assert metrics["gauges"].get("sync_games_total") == 3
    assert metrics["gauges"].get("sync_stores_count") == 2
