"""Tests for event_bus extension modules."""
from __future__ import annotations
import asyncio
import pytest
from unifideck.core.types import Events
from unifideck.event_bus.event_bus import EventBus

# ── DeadLetterQueue ──────────────────────────────────────────────────

class TestDeadLetterQueue:
    def test_record_and_snapshot(self):
        from unifideck.event_bus.event_bus_extensions import DeadLetterQueue
        dlq = DeadLetterQueue(max_size=5)
        # signature: record(self, event, payload, reason)
        dlq.record("sync_failed", {"k": "v"}, "boom")
        snap = dlq.snapshot()
        assert len(snap) == 1
        assert snap[0]["reason"] == "boom"
        assert snap[0]["event"] == "sync_failed"

# ── CircuitBreaker ──────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_starts_closed(self):
        from unifideck.event_bus.event_bus_reliability import CircuitBreaker
        cb = CircuitBreaker()
        assert not cb.is_open("h")

    def test_opens_after_threshold(self):
        from unifideck.event_bus.event_bus_reliability import CircuitBreaker
        # threshold 0.5, window 20 (default)
        cb = CircuitBreaker(open_threshold=0.5)
        for _ in range(20):
            cb.record("h", False)
        assert cb.is_open("h") is True

    def test_success_resets_to_closed(self):
        from unifideck.event_bus.event_bus_reliability import CircuitBreaker
        import time
        # Use a very high threshold so it doesn't trip, or just test allow()
        cb = CircuitBreaker(open_threshold=0.5, reset_timeout=0.0)
        for _ in range(20): cb.record("h", False)
        # Even with reset_timeout=0, it's open until we call allow()
        assert cb.allow("h") is True
        assert not cb.is_open("h")

# ── BatchDispatcher ──────────────────────────────────────────────────

class TestBatchDispatcher:
    def test_add_and_drain(self):
        from unifideck.event_bus.event_bus_scaling import BatchDispatcher
        bd = BatchDispatcher(max_size=50)
        bd.add("evt", {"i": 1})
        items = bd.drain("evt")
        assert len(items) == 1
        assert items[0]["i"] == 1

# ── Devex ────────────────────────────────────────────────────────────

class TestDevex:
    def test_schema_extractor(self):
        from unifideck.event_bus.event_bus_devex import SchemaExtractor
        source = 'bus.emit("sync_complete", store="epic", count=5)'
        schema = SchemaExtractor.extract_from_source(source)
        assert "sync_complete" in schema
        assert "store" in schema["sync_complete"]
        assert "count" in schema["sync_complete"]

    @pytest.mark.asyncio
    async def test_subscribe_and_auto_wire(self):
        from unifideck.event_bus.event_bus_devex import auto_wire, subscribe
        bus = EventBus()
        class Svc:
            def __init__(self): self.calls = []
            @subscribe(Events.GAME_INSTALLED)
            async def on_installed(self, **kw): self.calls.append(kw)
        svc = Svc()
        auto_wire(svc, bus)
        await bus.emit(Events.GAME_INSTALLED, title="Hades")
        assert len(svc.calls) == 1
        assert svc.calls[0]["title"] == "Hades"
