"""event_bus/event_bus.py — Asynchronous publish/subscribe EventBus.
Central nervous system of the 5-layer architecture. Eliminates the 70
circular `plugin_instance` back-references found in the legacy codebase
by decoupling publishers from subscribers: any component can emit an
event without knowing who listens; any component can subscribe to an
event without being imported by the publisher.
Features:
- Named events via the `Events` enum from core/types.py (23 events)
- Async handlers via `asyncio.iscoroutinefunction()` detection
- Persistent subscriptions via `on(event, handler)`
- One-shot subscriptions via `once(event, handler)` — auto-removed
- Subscription removal via `off(event, handler)`
- Error isolation: one failing handler never blocks the others
- Diagnostic logging: every emit() logs event name, handler count,
 per-handler duration and status
Design decisions:
- Handlers are stored per-event in a list (insertion order preserved)
- emit() runs all handlers concurrently via `asyncio.gather()` with
 `return_exceptions=True` so exceptions are captured, logged, and
 isolated
- Sync handlers are supported too — wrapped in `asyncio.to_thread()`
 automatically so they never block the loop
Reference: Technical Document v1.0 — Sections 3.2, 3.2.2, 3.2.3,
ADR-01.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.types import Events

logger = logging.getLogger(__name__)

# Handler signature: either async or sync, receives any kwargs payload.
Handler = Callable[..., Awaitable[Any]] | Callable[..., Any]


class EventBus:
    """Asynchronous pub/sub bus.
    Thread-safety note: this class is designed for single-event-loop use
    (asyncio). It is not thread-safe by design; all calls must happen on
    the same event loop as the one running the plugin.
    Usage:
    bus = EventBus()
    async def on_auth(store: str, **kw):
    print(f"auth complete for {store}")
    bus.on(Events.STORE_AUTH_COMPLETE, on_auth)
    await bus.emit(Events.STORE_AUTH_COMPLETE, store="epic").
    """

    def __init__(self) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        # Map Events.value -> list of handlers (str key avoids enum
        # identity issues across module reloads)
        self._handlers: dict[str, list[Handler]] = {}
        # Track one-shot handlers so we can remove them after the call
        self._once: dict[str, list[Handler]] = {}
        # ── Subscription API ────────────────────────────────────────

    def on(self, event: Events | str, handler: Handler) -> None:
        """Register a persistent handler for an event.
        The handler is called every time the event is emitted until
        explicitly removed via `off()`.
        """
        key = self._key(event)
        self._handlers.setdefault(key, []).append(handler)
        logger.debug("[EventBus] on(%s) -> %s handlers",
        key, len(self._handlers[key]))
    def once(self, event: Events | str, handler: Handler) -> None:
        """Register a one-shot handler for an event.
        The handler is called on the next emission of the event, then
        automatically removed. Useful for awaiting a single completion
        (e.g. one sync cycle).
        """
        key = self._key(event)
        self._handlers.setdefault(key, []).append(handler)
        self._once.setdefault(key, []).append(handler)
        logger.debug("[EventBus] once(%s)", key)
    def off(self, event: Events | str, handler: Handler) -> bool:
        """Unregister a handler.

        Returns True if removed, False if not found. Safe to
        call with handlers that were never registered.
        """
        key = self._key(event)
        if key not in self._handlers:
            return False
        try:
            self._handlers[key].remove(handler)
        except ValueError:
            return False
        # Also remove from `once` tracking if present
        if key in self._once and handler in self._once[key]:
            self._once[key].remove(handler)
        return True

    def clear(self, event: Events | None = None) -> None:
        """Remove all handlers for a specific event, or all events if
        `event` is None. Used in tests and on plugin shutdown.
        """
        if event is None:
            self._handlers.clear()
            self._once.clear()
            logger.debug("[EventBus] cleared all handlers")
        else:
            key = self._key(event)
            self._handlers.pop(key, None)
            self._once.pop(key, None)
            logger.debug("[EventBus] cleared %s", key)
    def handler_count(self, event: Events | str) -> int:
        """Return the number of handlers currently registered for an
        event. Useful for diagnostics and tests.
        """
        return len(self._handlers.get(self._key(event), []))
        # ── Emission API ────────────────────────────────────────────

    async def emit(self, event: Events | str, **payload: Any) -> list[Any]:
        """Emit an event to all registered handlers.

        Accepts both a ``Events`` enum member and the raw string
        value (same duck-typing as ``_key``). Legacy callers that
        pass strings directly keep working; new code should prefer
        the enum for IDE autocomplete.

        All handlers run concurrently via `asyncio.gather()`. Exceptions
        raised by any handler are captured (not raised), logged, and
        returned as part of the result list. This guarantees that one
        failing subscriber never blocks the others.
        Returns a list of results (one per handler, in registration
        order). Exception instances replace the result for failed
        handlers.
        """
        key = self._key(event)
        handlers = list(self._handlers.get(key, []))  # snapshot
        if not handlers:
            logger.debug("[DIAG] event=%s handlers=0", key)
            return []
        started = time.monotonic()
        logger.debug(
            "[DIAG] event=%s handlers=%d payload_keys=%s",
            key, len(handlers), list(payload.keys()),
        )
        # Wrap sync handlers so the whole list can be gathered
        tasks = [self._invoke(h, payload) for h in handlers]
        results = await asyncio.gather(
            *tasks, return_exceptions=True,
        )
        # Clean up one-shot handlers AFTER the emission so
        # they can't fire twice if re-emitted inside a handler
        once_list = self._once.get(key, [])
        if once_list:
            remaining = [
                h for h in self._handlers[key]
                if h not in once_list
            ]
            self._handlers[key] = remaining
            self._once[key] = []
        # Log per-handler status for diagnostics
        dt_total = (time.monotonic() - started) * 1000
        ok = sum(
            1 for r in results
            if not isinstance(r, Exception)
        )
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(
                    "[EventBus] handler #%d for %s failed: "
                    "%s: %s",
                    i, key, type(r).__name__, r,
                )
        logger.debug(
            "[DIAG] event=%s total=%.2fms success=%d/%d",
            key, dt_total, ok, len(results),
        )
        return results

    # ── Internals ──────────────────────────────────────
    async def _invoke(self, handler: Handler,
                      payload: dict[str, Any]) -> Any:
        """Invoke a single handler with the event payload.
        Async handlers are awaited directly. Sync handlers
        are run on a worker thread via `asyncio.to_thread()`
        so they never block the asyncio event loop even if
        they perform I/O.
        """
        if inspect.iscoroutinefunction(handler):
            return await handler(**payload)
        # Sync handler — offload to a thread
        return await asyncio.to_thread(handler, **payload)

    @staticmethod
    def _key(event: Events | str) -> str:
        """Return a string key for an event.
        Supports both `Events.STORE_AUTH_COMPLETE` and the raw
        string value `"auth_complete"` so callers can use
        either form. This keeps the bus resilient to module
        reloads during development.
        """
        if isinstance(event, Events):
            return event.value
        return str(event)
