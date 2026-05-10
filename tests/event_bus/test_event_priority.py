"""Tests for event_bus/event_priority.py (OP-09b)."""
from __future__ import annotations

from unifideck.core.types import Events
from unifideck.event_bus.event_priority import (
    COALESCE_KEY,
    EventPriority,
    _DEFAULT_PRIORITY,
    get_coalesce_key,
    get_priority,
)


def test_full_events_coverage():
    """Every Events member must have an entry in _DEFAULT_PRIORITY (CI/CD OP-76)."""
    missing = [e for e in Events if e not in _DEFAULT_PRIORITY]
    assert not missing, f"Events missing from _DEFAULT_PRIORITY: {missing}"


def test_critical_events():
    assert get_priority(Events.PLUGIN_LOADED) == EventPriority.CRITICAL
    assert get_priority(Events.GAME_LAUNCHED) == EventPriority.CRITICAL
    assert get_priority(Events.GAME_STOPPED) == EventPriority.CRITICAL


def test_background_events():
    assert get_priority(Events.SYNC_PROGRESS) == EventPriority.BACKGROUND
    assert get_priority(Events.DOWNLOAD_PROGRESS) == EventPriority.BACKGROUND
    assert get_priority(Events.STORE_ERROR) == EventPriority.BACKGROUND


def test_unknown_event_falls_back_to_normal():
    """Unknown events must not be BACKGROUND (could be silently dropped)."""
    assert get_priority("totally_unknown_event") == EventPriority.NORMAL


def test_coalesce_keys():
    assert get_coalesce_key(Events.SYNC_PROGRESS) == "store"
    assert get_coalesce_key(Events.DOWNLOAD_PROGRESS) == "download_id"
    assert get_coalesce_key(Events.GAME_LAUNCHED) == ""
    assert get_coalesce_key("unknown") == ""
