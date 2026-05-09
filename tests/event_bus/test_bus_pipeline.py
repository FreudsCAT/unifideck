"""Tests for event_bus/bus_pipeline.py."""
from __future__ import annotations
import asyncio
import pytest
from unifideck.event_bus.bus_pipeline import BusPipeline
from unifideck.event_bus.event_bus import EventBus
from unifideck.event_bus.event_replay import EventReplayBuffer
from unifideck.event_bus.priority_dispatcher import PriorityDispatcher
from unifideck.event_bus.event_bus_scaling import BatchDispatcher
from unifideck.event_bus.supervision.metrics_handler import HandlerLatencyCollector
from unifideck.event_bus.supervision.watchdog_handler import HandlerWatchdog

@pytest.mark.asyncio
async def test_pipeline_container():
    bus = EventBus()
    wd = HandlerWatchdog()
    lc = HandlerLatencyCollector()
    rb = EventReplayBuffer()
    bd = BatchDispatcher()
    pd = PriorityDispatcher(bus)
    
    pipeline = BusPipeline(wd, lc, rb, bd, pd)
    assert pipeline.watchdog == wd
    assert pipeline.dispatcher == pd

@pytest.mark.asyncio
async def test_dispatcher_lifecycle():
    bus = EventBus()
    pd = PriorityDispatcher(bus)
    await pd.start()
    pd.enqueue("test_event", key="val")
    await pd.stop()

@pytest.mark.asyncio
async def test_dispatcher_emit_integration():
    bus = EventBus()
    received = []
    async def handler(**kw): received.append(kw)
    bus.on("test_event", handler)
    
    pd = PriorityDispatcher(bus)
    await pd.start()
    pd.enqueue("test_event", key="val")
    
    # Wait for worker to process
    for _ in range(10):
        if len(received) > 0: break
        await asyncio.sleep(0.02)
        
    assert len(received) == 1
    assert received[0]["key"] == "val"
    await pd.stop()
