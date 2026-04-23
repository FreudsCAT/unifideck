"""event_bus/event_bus.py — Asynchronous publish/subscribe EventBus.

# OP-09a | event_bus/event_bus.py | Depends: OP-05, OP-09b, OP-09c

Central nervous system of the 5-layer architecture. Decouples
publishers from subscribers so any component can emit without
knowing who listens, and vice versa. Replaces 70+ circular
``plugin_instance`` back-references from the legacy codebase.

Single-loop asyncio only — not thread-safe. One failing handler
never blocks the others (errors captured via gather + logged).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..core.types import Events

logger = logging.getLogger(__name__)

Handler = Callable[..., Awaitable[Any]] | Callable[..., Any]


class EventBus:
    """Pub/sub bus with async emission + error isolation."""

    def __init__(self) -> None:
        """Init two empty handler dicts: one for persistent
        subscriptions, one tracking one-shot handlers for removal.
        Keys are event *string values* so the bus survives module
        reloads that would break enum identity.
        """
        self._handlers: dict[str, list[Handler]] = {}
        self._oneshot: set[int] = set()

    def on(self, event: Events | str, handler: Handler) -> None:
        """Register a persistent handler. Called on every emission
        until removed via ``off()``.
        """
        key = self._key(event)
        self._handlers.setdefault(key, []).append(handler)

    def once(self, event: Events | str, handler: Handler) -> None:
        """Register a one-shot handler. Auto-removed after the
        next emission. Useful for awaiting a single completion
        (one sync cycle, one auth flow).
        """
        key = self._key(event)
        self._handlers.setdefault(key, []).append(handler)
        self._oneshot.add(id(handler))

    def off(self, event: Events | str, handler: Handler) -> bool:
        """Unregister ``handler`` from ``event``. Return True if
        removed, False if not found. Safe on unknown handlers.
        """
        key = self._key(event)
        handlers = self._handlers.get(key)
        if handlers is None:
            return False
        try:
            handlers.remove(handler)
            self._oneshot.discard(id(handler))
            return True
        except ValueError:
            return False

    def clear(self, event: Events | None = None) -> None:
        """Remove all handlers for ``event``, or all events if None.
        Used on shutdown and in tests.
        """
        if event is None:
            self._handlers.clear()
            self._oneshot.clear()
        else:
            key = self._key(event)
            removed = self._handlers.pop(key, [])
            for h in removed:
                self._oneshot.discard(id(h))

    def handler_count(self, event: Events | str) -> int:
        """Return number of handlers currently registered for ``event``."""
        return len(self._handlers.get(self._key(event), []))

    async def emit(self, event: Events | str, **payload: Any) -> list[Any]:
        """Emit ``event`` to all registered handlers.

        Runs handlers concurrently via ``asyncio.gather(..., return_exceptions=True)``.
        Returns results in registration order — exceptions replace
        results for failed handlers. One-shot handlers are removed
        AFTER emission so they can't fire twice if re-emitted inside
        a handler. Logs per-handler timing and success count at DEBUG.
        """
        key = self._key(event)
        handlers = list(self._handlers.get(key, []))
        if not handlers:
            return []

        tasks = [self._invoke(h, payload) for h in handlers]
        t0 = time.monotonic()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed_ms = (time.monotonic() - t0) * 1000

        ok_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "[EventBus] Handler %s failed on %s: %s",
                    getattr(handlers[i], "__qualname__", handlers[i]),
                    key, result,
                )
            else:
                ok_count += 1

        logger.debug(
            "[EventBus] %s: %d/%d handlers OK in %.1fms",
            key, ok_count, len(handlers), elapsed_ms,
        )

        # Remove one-shot handlers AFTER emission
        to_remove = [h for h in handlers if id(h) in self._oneshot]
        for h in to_remove:
            self._oneshot.discard(id(h))
            current = self._handlers.get(key)
            if current:
                try:
                    current.remove(h)
                except ValueError:
                    pass

        return results

    async def _invoke(
        self, handler: Handler, payload: dict[str, Any],
    ) -> Any:
        """Call one handler with the payload.
        Async handlers are awaited directly. Sync handlers are
        offloaded via ``asyncio.to_thread`` so blocking I/O never
        freezes the event loop.
        """
        if asyncio.iscoroutinefunction(handler):
            return await handler(**payload)
        else:
            return await asyncio.to_thread(handler, **payload)

    @staticmethod
    def _key(event: Events | str) -> str:
        """Normalise an event reference to its string value.
        Accepts both ``Events.FOO`` and the raw ``"foo"`` so legacy
        callers passing strings keep working.
        """
        if hasattr(event, "value"):
            return event.value
        return str(event)
