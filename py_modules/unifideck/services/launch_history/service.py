"""Launch history service orchestration.

OP-21a | py_modules/unifideck/services/launch_history/service.py

``LaunchHistoryService`` composes :

* ``_FailuresMixin`` — record + query failures;
* ``_BypassMixin``   — temporary "force allow" overrides (user can
  override the circuit breaker for one launch);

Public API : ``record_launch_outcome``, ``recent_failures_for``,
``is_failing(game)``, ``request_bypass(game)``, etc.

Persistence is delegated to ``persistence`` and the history is
loaded once at boot.
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
    """Launch history service."""

    DEFAULT_THRESHOLD = DEFAULT_THRESHOLD
    DEFAULT_WINDOW_SECONDS = DEFAULT_WINDOW_SECONDS
    DEFAULT_FAST_BOOT_SECONDS = DEFAULT_FAST_BOOT_SECONDS

    def __init__(
        self,
        config: Any | None = None,
        storage_path: Path | None = None,
        bus: Any | None = None,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        if storage_path is None:
            storage_path = (
                Path.home() / ".local" / "share" / "unifideck" / "launch_history.json"
            )
        self._path = Path(storage_path)
        self._bus = bus

    def threshold(self) -> int:
        """Threshold."""
        return read_threshold(self._config)

    def window_seconds(self) -> float:
        """Window seconds."""
        return read_window_seconds(self._config)

    def fast_boot_seconds(self) -> float:
        """Fast boot seconds."""
        return read_fast_boot_seconds(self._config)

    def _emit_state(self, game_key: str, trigger: str) -> None:
        """Emit state."""
        if self._bus is None:
            return
        try:
            recent = self.get_recent_failures(game_key)
            payload = {
                "game_key": game_key,
                "state": "open" if self.is_circuit_open(game_key) else "closed",
                "recent_count": len(recent),
                "failure_kinds": [f.get("kind", "unknown") for f in recent],
                "trigger": trigger,
            }
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._bus.emit(
                        Events.CIRCUIT_STATE_CHANGED,
                        **payload,
                    ),
                    name=f"circuit-event-{game_key}",
                )
            except RuntimeError:
                pass
        except Exception:
            logger.warning(
                "[LaunchHistory] _emit_state failed for %s/%s",
                game_key,
                trigger,
            )

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs) -> None:
        """On game stopped."""
        store = kwargs.get("store", "")
        game_id = kwargs.get("game_id", "")
        exit_code = kwargs.get("exit_code", 0)
        elapsed = kwargs.get("elapsed_seconds", 0.0)
        terminated_by_signal = kwargs.get("terminated_by_signal", False)
        if not store or not game_id:
            return
        game_key = f"{store}:{game_id}"
        if exit_code == 0:
            self.record_success(game_key)
            return
        if terminated_by_signal:
            return
        if exit_code != 0 and elapsed < self.fast_boot_seconds():
            self.record_failure(game_key, FAILURE_KIND_FAST_BOOT)
