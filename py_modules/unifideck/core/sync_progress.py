"""SyncProgress — per-phase progress tracker for the frontend bar.

Ported from ``staging:main.py:1496-1650``. One instance per sync run,
owned by ``SyncService``. Each pipeline phase owns a percentage
range; within a phase, progress is computed from its sub-counter
(done / total). The frontend polls ``get_sync_progress`` → this
object's ``to_dict()`` every 500 ms.

Phase transitions are triggered by the synchronous pass-through
methods so callers (SyncService itself, ArtworkService,
MetadataService) never set internal fields directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Percentage allocation per phase. The progress bar always moves
# forward monotonically — once a phase is done, its range is
# committed and the next phase starts at the next boundary.
PHASE_RANGES: dict[str, tuple[int, int]] = {
    "idle": (0, 0),
    "fetching": (0, 10),
    "checking_installed": (10, 20),
    "syncing": (20, 40),
    "steam_metadata": (40, 50),
    "unifidb_lookup": (50, 55),
    "sgdb_lookup": (55, 60),
    "artwork": (60, 90),
    "metadata": (90, 98),
    "complete": (100, 100),
    "error": (100, 100),
    "cancelled": (100, 100),
}


class SyncProgress:
    """Per-sync-run progress tracker."""

    def __init__(self) -> None:
        self.total_games: int = 0
        self.synced_games: int = 0
        self.current_game: dict[str, Any] = {"label": None, "values": {}}
        self.status: str = "idle"
        self.error: str | None = None
        self.progress_percent: int = 0
        # Per-phase sub-counters.
        self.artwork_total: int = 0
        self.artwork_synced: int = 0
        self.steam_total: int = 0
        self.steam_synced: int = 0
        self.metadata_total: int = 0
        self.metadata_synced: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()

    # ── Phase-entry helpers ────────────────────────────────────

    def start_fetching(self, store_count: int) -> None:
        self.status = "fetching"
        self.total_games = 0
        self.current_game = {
            "label": "sync.fetchingGameLists",
            "values": {"count": store_count},
        }

    def start_store_sync(self, store_name: str, idx: int, total: int) -> None:
        self.status = "syncing"
        self.current_game = {
            "label": "sync.fetchingStore",
            "values": {"store": store_name, "current": idx + 1, "total": total},
        }

    def set_library_totals(self, total_games: int) -> None:
        self.total_games = total_games
        self.synced_games = total_games

    def start_artwork(
        self, total: int, label: str = "artwork.checking",
    ) -> None:
        self.status = "artwork"
        self.artwork_total = total
        self.artwork_synced = 0
        self.current_game = {
            "label": label,
            "values": {"synced": 0, "total": total},
        }

    def start_metadata(
        self, total: int, label: str = "sync.extractingMetadata",
    ) -> None:
        self.status = "metadata"
        self.metadata_total = total
        self.metadata_synced = 0
        self.current_game = {
            "label": label,
            "values": {"synced": 0, "total": total},
        }

    def mark_complete(self) -> None:
        self.status = "complete"
        self.current_game = {"label": "sync.completed", "values": {}}
        self.progress_percent = 100

    def mark_error(self, error: str) -> None:
        self.status = "error"
        self.error = error
        self.progress_percent = 100

    def mark_cancelled(self) -> None:
        self.status = "cancelled"
        self.progress_percent = 100

    # ── Per-game increment helpers (thread-safe) ──────────────

    async def increment_artwork(self, title: str) -> int:
        async with self._lock:
            self.artwork_synced += 1
            self.current_game = {
                "label": "sync.downloadingArtwork",
                "values": {
                    "synced": self.artwork_synced,
                    "total": self.artwork_total,
                    "game": title,
                },
            }
            self._recalc()
            return self.artwork_synced

    async def increment_metadata(self, title: str) -> int:
        async with self._lock:
            self.metadata_synced += 1
            self.current_game = {
                "label": "sync.extractingMetadata",
                "values": {
                    "synced": self.metadata_synced,
                    "total": self.metadata_total,
                    "game": title,
                },
            }
            self._recalc()
            return self.metadata_synced

    async def increment_steam(self, title: str) -> int:
        async with self._lock:
            self.steam_synced += 1
            self.current_game = {
                "label": "sync.extractingSteamMetadata",
                "values": {
                    "synced": self.steam_synced,
                    "total": self.steam_total,
                    "game": title,
                },
            }
            self._recalc()
            return self.steam_synced

    # ── Internal ──────────────────────────────────────────────

    def _recalc(self) -> None:
        """Compute progress_percent from the current phase + sub-counter."""
        start_pct, end_pct = PHASE_RANGES.get(self.status, (0, 0))
        span = end_pct - start_pct

        if self.status == "artwork" and self.artwork_total > 0:
            sub = self.artwork_synced / self.artwork_total
            self.progress_percent = int(start_pct + span * sub)
        elif self.status == "metadata" and self.metadata_total > 0:
            sub = self.metadata_synced / self.metadata_total
            self.progress_percent = int(start_pct + span * sub)
        elif self.status == "syncing" and self.total_games > 0:
            sub = self.synced_games / self.total_games
            self.progress_percent = int(start_pct + span * sub)
        else:
            self.progress_percent = start_pct

    def to_dict(self) -> dict[str, Any]:
        self._recalc()
        return {
            "success": True,
            "total_games": self.total_games,
            "synced_games": self.synced_games,
            "current_game": self.current_game,
            "status": self.status,
            "progress_percent": self.progress_percent,
            "error": self.error,
            "artwork_total": self.artwork_total,
            "artwork_synced": self.artwork_synced,
            "steam_total": self.steam_total,
            "steam_synced": self.steam_synced,
            "unifidb_total": 0,
            "unifidb_synced": 0,
            "metacritic_total": self.metadata_total,
            "metacritic_synced": self.metadata_synced,
        }
