"""Data models and enums for Unifideck activity tracking."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List


class EndReason(str, Enum):
    NORMAL = "normal"
    CRASH = "crash"
    SUSPEND = "suspend"
    SHUTDOWN = "shutdown"
    PLUGIN_UNLOAD = "plugin_unload"
    FORCE_STOP = "force_stop"
    STEAM_EXIT = "steam_exit"
    UNKNOWN = "unknown"


class GameEventType(str, Enum):
    INSTALLED = "installed"
    UNINSTALLED = "uninstalled"
    UPDATED = "updated"
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_COMPLETED = "download_completed"
    DOWNLOAD_FAILED = "download_failed"
    DOWNLOAD_CANCELLED = "download_cancelled"
    CLOUD_SAVE_UPLOADED = "cloud_save_uploaded"
    CLOUD_SAVE_DOWNLOADED = "cloud_save_downloaded"
    CLOUD_SAVE_CONFLICT = "cloud_save_conflict"
    SHORTCUT_CREATED = "shortcut_created"
    SHORTCUT_REMOVED = "shortcut_removed"
    PROTON_CHANGED = "proton_changed"
    ARTWORK_FETCHED = "artwork_fetched"
    FIRST_LAUNCH = "first_launch"
    MOVED = "moved"


class DeviceEventType(str, Enum):
    PLUGIN_LOADED = "plugin_loaded"
    PLUGIN_UNLOADED = "plugin_unloaded"
    DEVICE_SUSPEND = "device_suspend"
    DEVICE_RESUME = "device_resume"
    STEAM_RESTART = "steam_restart"
    LIBRARY_SYNC_STARTED = "library_sync_started"
    LIBRARY_SYNC_COMPLETED = "library_sync_completed"
    LIBRARY_SYNC_FAILED = "library_sync_failed"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    ACCOUNT_SWITCH = "account_switch"
    STORE_CONNECTED = "store_connected"
    STORE_DISCONNECTED = "store_disconnected"


@dataclass
class ActiveSession:
    """In-memory representation of a currently active play session."""
    game_db_id: int
    store: str
    store_game_id: str
    steam_app_id: int
    title: str
    started_at: datetime
    proton_tool: Optional[str] = None
    db_row_id: Optional[int] = None  # Set after INSERT
    suspended_at: Optional[datetime] = None  # Track suspend time
    total_sleep_secs: float = 0.0  # Accumulated sleep gap


@dataclass
class PlaySessionResult:
    """Query result for a play session."""
    id: int
    game_id: int
    started_at: str
    ended_at: Optional[str]
    duration_secs: Optional[int]
    end_reason: str
    title: str
    store: str
    proton_tool: Optional[str] = None
    is_manual: bool = False


@dataclass
class GameStatsResult:
    """Pre-computed lifetime stats for a game."""
    game_id: int
    title: str
    store: str
    steam_app_id: Optional[int]
    total_secs: int
    total_sessions: int
    avg_session_secs: int
    min_session_secs: Optional[int]
    max_session_secs: int
    first_played_at: Optional[str]
    last_played_at: Optional[str]
    current_streak_days: int
    longest_streak_days: int


@dataclass
class DailyTotal:
    """Daily aggregate stats."""
    date: str
    total_secs: int
    session_count: int
    games_played: int


@dataclass
class StoreSummary:
    """Per-store aggregate stats."""
    store: str
    total_secs: int
    game_count: int
    session_count: int
    most_played_title: Optional[str]
    most_played_secs: int


@dataclass
class OverallStats:
    """Dashboard-level aggregate stats."""
    total_secs: int
    total_sessions: int
    total_games_played: int
    most_active_hour: Optional[int]
    most_active_day: Optional[str]
    average_daily_secs: int
    this_week_secs: int
    last_week_secs: int
