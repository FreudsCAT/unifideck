from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus
from .correlation import get_launch_id

logger = logging.getLogger(__name__)
LAUNCH_PHASE_TIMING_EVENT = "LAUNCH_PHASE_TIMING"
class PhaseTimer:
    """Phase timer."""
    __slots__ = ("_bus", "_extra", "_phase", "_t0")
    def __init__(
        self,
        bus: EventBus,
        phase: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._phase = phase
        self._extra = dict(extra) if extra else {}
        self._t0 = 0.0
    async def __aenter__(self) -> PhaseTimer:
        """Aenter."""
        self._t0 = time.monotonic()
        return self
    async def __aexit__(
        self, exc_type: Any, _exc_val: Any, _exc_tb: Any,
    ) -> None:
        """Aexit."""
        duration_ms = int((time.monotonic() - self._t0) * 1000)
        payload = {
            "phase": self._phase,
            "duration_ms": duration_ms,
            "launch_id": get_launch_id(),
            "success": exc_type is None,
            **self._extra,
        }
        if self._bus is not None:
            try:
                await self._bus.emit(
                    LAUNCH_PHASE_TIMING_EVENT, **payload,
                )
            except Exception:
                logger.exception(
                    "[telemetry] emit failed for phase=%s",
                    self._phase,
                )
        logger.debug(
            "[telemetry] phase=%s duration_ms=%d success=%s",
            self._phase, duration_ms, exc_type is None,
        )
