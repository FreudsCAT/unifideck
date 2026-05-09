"""Tests for services/playtime/service.py."""
import os
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from unifideck.services.playtime.service import PlaytimeService
from unifideck.core.types import Events

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "playtime.db")

@pytest.fixture
def bus():
    bus = MagicMock()
    return bus

@pytest.fixture
def service(bus, db_path):
    svc = PlaytimeService(bus, db_path)
    # Mock auto_wire to avoid actual wiring
    svc._bus.auto_wire = MagicMock()
    return svc

@pytest.mark.asyncio
async def test_session_start_stop(service):
    await service.start()
    
    # Simulate GAME_LAUNCHED
    await service._on_game_launched(
        store="epic",
        game_id="hades",
        title="Hades",
        app_id=12345
    )
    
    assert "epic:hades" in service._active
    session = service._active["epic:hades"]
    assert session["title"] == "Hades"
    
    # Wait a bit
    import asyncio
    # Set the start time back so we have enough duration
    session["started_at"] = datetime.now(timezone.utc) - timedelta(seconds=10)
    
    # Simulate GAME_STOPPED
    await service._on_game_stopped(store="epic", game_id="hades")
    
    assert "epic:hades" not in service._active
    
    # Check DB
    stats = await service.get_playtime("epic", "hades")
    assert stats["total_seconds"] >= 10
    assert stats["session_count"] == 1

@pytest.mark.asyncio
async def test_suspend_resume(service):
    await service.start()
    
    await service._on_game_launched(store="gog", game_id="cyberpunk", title="Cyberpunk")
    session = service._active["gog:cyberpunk"]
    
    # Suspend
    await service._on_suspend()
    assert session["suspended_at"] is not None
    
    # Simulate 1 hour of sleep
    session["suspended_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
    
    # Resume
    await service._on_resume()
    assert session["suspended_at"] is None
    assert session["total_sleep_secs"] >= 3600
    
    # End session
    session["started_at"] = datetime.now(timezone.utc) - timedelta(hours=2)
    await service._on_game_stopped(store="gog", game_id="cyberpunk")
    
    stats = await service.get_playtime("gog", "cyberpunk")
    # Total wall time 2h, sleep 1h -> play time ~1h (3600s)
    assert 3590 < stats["total_seconds"] < 3610

@pytest.mark.asyncio
async def test_streaks(service):
    await service.start()
    db = service._db
    
    # Manually insert historical data for a 3-day streak
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    
    game_db_id = db.get_or_create_game("steam", "123", "Test Game", 123)
    
    # Insert daily stats
    for d in [yesterday, day_before]:
        db.execute(
            "INSERT INTO daily_stats (game_id, date, total_secs, session_count) VALUES (?, ?, ?, ?)",
            (game_db_id, d.strftime("%Y-%m-%d"), 3600, 1)
        )
    db.conn.commit()
    
    # Play today
    await service._on_game_launched(store="steam", game_id="123", title="Test Game")
    session = service._active["steam:123"]
    session["started_at"] = datetime.now(timezone.utc) - timedelta(minutes=10)
    await service._on_game_stopped(store="steam", game_id="123")
    
    stats = await service.get_playtime("steam", "123")
    assert stats["current_streak"] == 3
    assert stats["longest_streak"] >= 3
