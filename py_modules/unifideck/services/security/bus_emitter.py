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
    """Fire-and-forget bus emission of a security event.

    Used from synchronous contexts (mixin callbacks, brute-force
    detector) that can't ``await`` the bus emission. Schedules
    the emission on the running asyncio loop and returns
    immediately.

    Three defensive layers:

    * **No running loop** — silently return (the caller is in a
      pure-sync context with no event loop, typical for early
      boot or in some tests).
    * **Unknown event name** — caught by the outer ``except``
      and logged at DEBUG; emission is dropped.
    * **Bus emission failure** — also caught and logged.

    The emission is best-effort: a security event missed because
    the loop wasn't running isn't a critical failure, the audit
    log has already recorded the underlying event.

    Args:
        bus: live event bus.
        event_name: name of the ``Events`` member (looked up via
            ``getattr``).
        **kwargs: payload forwarded to ``bus.emit``.
    """
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
