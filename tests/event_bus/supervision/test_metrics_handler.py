"""Tests for event_bus/supervision/metrics_handler.py (OP-10b)."""
from __future__ import annotations

from unifideck.event_bus.supervision.metrics_handler import (
    HandlerLatencyCollector,
    HandlerLatencyStats,
)


def test_single_measurement():
    s = HandlerLatencyStats(name="h")
    s.record(10.0)
    assert s.invocations == 1
    assert s.total_ms == 10.0
    assert s.max_ms == 10.0
    assert s.p50_ms == 10.0
    assert s.p95_ms == 10.0


def test_multiple_measurements():
    s = HandlerLatencyStats(name="h")
    for i in range(100):
        s.record(float(i))
    assert s.invocations == 100
    assert s.max_ms == 99.0
    assert s.p50_ms > 0
    assert s.p95_ms >= s.p50_ms


def test_to_dict_excludes_window():
    s = HandlerLatencyStats(name="test")
    s.record(5.0)
    d = s.to_dict()
    assert "name" in d
    assert "avg_ms" in d
    assert "_window" not in d
    assert d["avg_ms"] == 5.0


def test_collector_lazy_creates():
    lc = HandlerLatencyCollector()
    lc.record("handler_a", 10.0)
    lc.record("handler_a", 20.0)
    snap = lc.get_snapshot()
    assert "handler_a" in snap
    assert snap["handler_a"]["invocations"] == 2


def test_collector_top_n():
    lc = HandlerLatencyCollector()
    lc.record("fast", 1.0)
    lc.record("slow", 100.0)
    lc.record("medium", 50.0)
    top = lc.get_top_n(2)
    keys = list(top.keys())
    assert keys[0] == "slow"
    assert len(top) == 2


def test_collector_reset():
    lc = HandlerLatencyCollector()
    lc.record("h", 5.0)
    assert lc.reset("h") is True
    assert lc.reset("h") is False
    assert "h" not in lc.get_snapshot()
