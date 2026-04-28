"""services.security.bus_emitter — Fire-and-forget SECURITY_* emitter.

Free function ``emit_security_event`` that fires a SECURITY_*
event onto the EventBus from synchronous code (handlers and
policies that need to emit without awaiting).

Extracted from ``security_service._emit`` on 2026-04-18 because
the logic is entirely generic: schedule ``bus.emit`` on the
running loop, swallow errors, log at debug. None of the 4
callers (brute-force callback, permissions repair,
device-reset, fingerprint-initialized) care about the task
result — they just want the event to reach subscribers.
"""
from __future__ import annotations

import asyncio
import logging

from ...core.types.events import Events
from ...event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)


def emit_security_event(
    bus: EventBus, event_name: str, **kwargs: object,
) -> None:
    """Fire-and-forget emit of a SECURITY_* event.

    Schedules ``bus.emit`` on the running event loop without
    awaiting. Safe to call from synchronous code paths. All
    failures are swallowed at debug level — the emitter is
    purely observational, so its failure must never break the
    code path being observed.

    Args:
        bus: The EventBus instance to emit on.
        event_name: Symbolic name of the event (e.g.
            ``"SECURITY_PERMISSIONS_REPAIRED"``). Resolved via
            ``getattr(Events, event_name)``.
        **kwargs: Forwarded to ``bus.emit`` as the event payload.
    """
    try:
        event = getattr(Events, event_name)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. unit test calling sync code
            # outside an async context). Skip — the event would
            # have no subscribers in that environment anyway.
            return
        loop.create_task(
            bus.emit(event, **kwargs),
            name=f"security-emit-{event_name}",
        )
    except Exception as e:  # noqa: BLE001 — intentional: defensive catch logged below
        logger.debug(
            "[bus_emitter] emit %s failed: %s",
            event_name, e,
        )
