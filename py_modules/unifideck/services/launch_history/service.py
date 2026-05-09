"""services/launch_history/service.py — Per-game launch failure tracking.

Circuit breaker for game launches: N failures within a sliding
window → refuse subsequent launches until window expires or user
resets. Distinct from ``PlaytimeService`` (permanent session
tracking) — failures are ephemeral and window-bounded.
Storage: ``~/.local/share/unifideck/launch_history.json``, atomic
writes. Filesystem-as-IPC between the out-of-process launcher
(writer) and the plugin (reader).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ...core.types.events import Events
from ...event_bus.event_bus_devex import subscribe
from .bypass import _BypassMixin
from .config_readers import (
    DEFAULT_FAST_BOOT_SECONDS,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_SECONDS,
    read_fast_boot_seconds,
    read_threshold,
    read_window_seconds,
)
from .constants import FAILURE_KIND_FAST_BOOT
from .failures import _FailuresMixin

logger = logging.getLogger(__name__)


class LaunchHistoryService(_FailuresMixin, _BypassMixin):
    """Tracks per-game launch failures within a sliding window."""

    # Backwards-compat class attrs — source of truth in config_readers.py.
    DEFAULT_THRESHOLD = DEFAULT_THRESHOLD
    DEFAULT_WINDOW_SECONDS = DEFAULT_WINDOW_SECONDS
    DEFAULT_FAST_BOOT_SECONDS = DEFAULT_FAST_BOOT_SECONDS

    def __init__(
        self,
        config: Any | None = None,
        storage_path: Path | None = None,
        bus: Any | None = None,
    ) -> None:
        """Store refs; no I/O at construction."""
        self._config = config
        
        if storage_path is None:
            self._path = Path("~/.local/share/unifideck/launch_history.json").expanduser()
        else:
            self._path = storage_path
            
        self._bus = bus
        
        if self._bus and hasattr(self._bus, "auto_wire"):
            self._bus.auto_wire(self)

    def threshold(self) -> int:
        """Live read of ``circuit_breaker.failures_threshold``."""
        return read_threshold(self._config)

    def window_seconds(self) -> float:
        """Live read of ``circuit_breaker.window_seconds``."""
        return read_window_seconds(self._config)

    def fast_boot_seconds(self) -> float:
        """Live read of ``circuit_breaker.fast_boot_seconds``."""
        return read_fast_boot_seconds(self._config)

    def _emit_state(self, game_key: str, trigger: str) -> None:
        """Fire-and-forget ``CIRCUIT_STATE_CHANGED`` on the bus."""
        if not self._bus:
            return
            
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No running loop
            
        async def _emit() -> None:
            try:
                is_open, count = self.is_circuit_open(game_key)
                store, game_id = game_key.split(":", 1)
                
                self._bus.emit(
                    Events.CIRCUIT_STATE_CHANGED,
                    store=store,
                    game_id=game_id,
                    is_open=is_open,
                    failure_count=count,
                    trigger=trigger,
                )
            except Exception as e:
                logger.warning("[LaunchHistory] Failed to emit circuit state: %s", e)
                
        loop.create_task(_emit())

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs: Any) -> None:
        """Classify a finished launch for the circuit breaker."""
        store = kwargs.get("store")
        game_id = kwargs.get("game_id")
        rc = kwargs.get("rc")
        elapsed = kwargs.get("elapsed", 0.0)
        
        if not store or not game_id:
            return
            
        game_key = f"{store}:{game_id}"
        
        # Determine success or failure
        if rc == 0:
            self.record_success(game_key)
            return
            
        if rc is None:
            return
            
        # Ignore if terminated by signal (user cancel)
        # Shell convention: > 128 is signal
        if rc > 128:
            logger.debug("[LaunchHistory] Ignoring signal termination %d for %s", rc, game_key)
            return
            
        # Non-zero exit code
        if elapsed < self.fast_boot_seconds():
            self.record_failure(game_key, FAILURE_KIND_FAST_BOOT, f"rc={rc}")
        else:
            logger.debug(
                "[LaunchHistory] Ignoring non-zero rc=%d for %s (ran for %.1fs >= fast_boot %.1fs)",
                rc, game_key, elapsed, self.fast_boot_seconds()
            )
