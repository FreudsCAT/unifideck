"""Tests for event_bus/event_replay.py."""
from __future__ import annotations
from unifideck.core.types import Events
from unifideck.event_bus.event_replay import EventReplayBuffer

def test_record_and_snapshot():
    buf = EventReplayBuffer()
    buf.record(Events.GAME_INSTALLED, {"title": "Hades"})
    snap = buf.snapshot()
    assert len(snap) == 1
    assert snap[0]["event"] == "game_installed"
    assert snap[0]["kwargs"]["title"] == "Hades"

def test_snapshot_newest_first():
    buf = EventReplayBuffer()
    buf.record(Events.SYNC_STARTED, {"idx": 1})
    buf.record(Events.SYNC_COMPLETE, {"idx": 2})
    snap = buf.snapshot()
    assert len(snap) == 2
    assert snap[0]["kwargs"]["idx"] == 2

def test_snapshot_filter_by_type():
    buf = EventReplayBuffer()
    buf.record(Events.SYNC_PROGRESS, {"store": "epic"})
    buf.record(Events.GAME_INSTALLED, {"title": "X"})
    snap = buf.snapshot(events=[Events.SYNC_PROGRESS])
    assert len(snap) == 1
    assert snap[0]["event"] == "sync_progress"

def test_ring_buffer_cap():
    buf = EventReplayBuffer(fallback_cap=3)
    for i in range(10):
        buf.record(Events.STORE_ERROR, {"idx": i})
    snap = buf.snapshot(events=[Events.STORE_ERROR])
    assert len(snap) <= 3

def test_clear_all():
    buf = EventReplayBuffer()
    buf.record(Events.SYNC_STARTED, {})
    buf.clear()
    assert buf.snapshot() == []

def test_timestamp_present():
    buf = EventReplayBuffer()
    buf.record(Events.PLUGIN_LOADED, {})
    snap = buf.snapshot()
    assert "timestamp" in snap[0]
    assert isinstance(snap[0]["timestamp"], float)
