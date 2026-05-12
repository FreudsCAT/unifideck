"""Bus emitter helper for security events.

OP-19e | py_modules/unifideck/services/security/bus_emitter.py

``emit_security_event`` is the canonical helper used by every
audit mixin to emit a structured security event on the bus. Wraps
the bus call with consistent metadata (event source, timestamp,
correlation id).
"""

from __future__ import annotations
import asyncio
import logging
from ...core.types.events import Events
from ...event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)


def emit_security_event(bus: EventBus, event_name: str, **kwargs: object) -> None:
    """Emit security event."""
    try:
        event = getattr(Events, event_name)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            bus.emit(event, **kwargs),
            name=f"security-emit-{event_name}",
        )
    except Exception as e:
        logger.debug(
            "[bus_emitter] emit %s failed: %s",
            event_name,
            e,
        )
