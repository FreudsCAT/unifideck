"""Event bus — core asynchronous pub/sub primitive.

OP-09a | py_modules/unifideck/event_bus/event_bus.py

``EventBus`` is the central pub/sub primitive of the plugin.
Every asynchronous interaction between services, RPC mixins and
stores flows through it.

Public API:

* ``on(event, handler)``      — register a persistent handler;
* ``once(event, handler)``    — register a one-shot handler that
  is auto-removed after the first emission;
* ``off(event, handler)``     — explicit unsubscribe;
* ``clear(event)``            — drop every handler for a given
  event, or every handler at all if ``event=None``;
* ``handler_count(event)``    — observability helper;
* ``emit(event, **payload)``  — fan-out to every subscriber,
  awaiting all in parallel.

The bus stores handlers as plain lists per event key (event
value or string). One-shot handlers are tracked separately in
``_once`` and pruned from ``_handlers`` after each emission.

Concurrency: ``emit`` schedules every handler concurrently via
``asyncio.gather(return_exceptions=True)`` so one slow handler
cannot block siblings, and one raising handler cannot abort the
emission for the others. Per-handler failures are logged at ERROR
but not propagated.

Sync handlers are supported via ``asyncio.to_thread`` so a
legacy callback that wasn't migrated to async still works
without blocking the event loop.

The core ``EventBus`` only implements basic subscribe/emit
semantics — circuit breaker, batching, replay, dead-letter queue
all live in sibling modules (``event_bus_reliability``,
``event_bus_scaling``, ``event_bus_extensions``, ``event_replay``)
and are wired together by ``BusPipeline``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from unifideck.core.types import Events

logger = logging.getLogger(__name__)
Handler = Callable[..., Awaitable[Any]] | Callable[..., Any]


class EventBus:
    """In-process async pub/sub with persistent + one-shot subscriptions."""

    def __init__(self) -> None:
        """Initialise empty subscriber tables.

        Two parallel dicts:

        * ``_handlers`` — the canonical ``event_key → [handler]``
          mapping; emissions iterate this list.
        * ``_once``     — subset of ``_handlers`` flagged for
          auto-removal after the next emission.
        """
        self._handlers: dict[str, list[Handler]] = {}
        self._once: dict[str, list[Handler]] = {}

    def on(self, event: Events | str, handler: Handler) -> None:
        """Register a persistent handler for ``event``.

        Multiple subscribers can register for the same event;
        they're all invoked in registration order (insertion
        into the list). Duplicate registrations are allowed — the
        same handler will be invoked twice per emission. The
        caller is responsible for de-dup if that matters.

        Args:
            event: ``Events`` enum value or string equivalent.
            handler: sync or async callable. Sync ones run on a
                thread pool via ``asyncio.to_thread``.
        """
        key = self._key(event)
        self._handlers.setdefault(key, []).append(handler)
        logger.debug("[EventBus] on(%s) -> %s handlers", key, len(self._handlers[key]))

    def once(self, event: Events | str, handler: Handler) -> None:
        """Register a one-shot handler that's removed after the next emission.

        The handler is appended to both ``_handlers`` (so the
        emission finds it) and ``_once`` (so the post-emit
        pruning logic in ``emit`` knows to drop it). Useful for
        await-style patterns where a caller wants to react to
        the next occurrence and forget about it.

        Args:
            event: ``Events`` enum value or string equivalent.
            handler: sync or async callable.
        """
        key = self._key(event)
        self._handlers.setdefault(key, []).append(handler)
        self._once.setdefault(key, []).append(handler)
        logger.debug("[EventBus] once(%s)", key)

    def off(self, event: Events | str, handler: Handler) -> bool:
        """Remove a specific handler from ``event``'s subscriber list.

        Removes the handler from both ``_handlers`` and (if
        present) ``_once``. Identity-based: only the exact
        ``handler`` reference is removed, even if duplicates
        exist (only the first occurrence is removed per call).

        Args:
            event: ``Events`` enum value or string equivalent.
            handler: the callable to remove.

        Returns:
            ``True`` if the handler was found and removed,
            ``False`` if it wasn't registered (no-op).
        """
        key = self._key(event)
        if key not in self._handlers:
            return False
        try:
            self._handlers[key].remove(handler)
        except ValueError:
            return False
        if key in self._once and handler in self._once[key]:
            self._once[key].remove(handler)
        return True

    def clear(self, event: Events | None = None) -> None:
        """Drop every handler for one event, or for every event.

        Useful for test teardown where you want a fresh bus
        without rebuilding the instance, or for hot-reload paths
        that need to wipe stale subscriptions before re-wiring.

        Args:
            event: optional event to scope the clear to. ``None``
                (default) wipes every event's handlers and the
                ``_once`` table entirely.
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
        """Return the number of subscribers currently registered for ``event``.

        Used by tests (assert one or more subscribers exist
        before triggering an event) and by diagnostics (the
        QAM debug panel can show "no subscribers" warnings).

        Args:
            event: ``Events`` enum value or string equivalent.

        Returns:
            Subscriber count for that event; 0 if none.
        """
        return len(self._handlers.get(self._key(event), []))

    async def emit(self, event: Events | str, **payload: Any) -> list[Any]:
        """Fan-out an event to every registered handler in parallel.

        Pipeline:

        1. Snapshot the current handlers list (immune to
           concurrent mutations during the emission).
        2. Schedule every handler via ``_invoke`` (which
           dispatches sync vs async) and gather concurrently
           with ``return_exceptions=True``.
        3. After the gather completes, prune any one-shot
           handlers from the canonical ``_handlers`` list.
        4. Log per-handler exceptions at ERROR; the emission
           still returns the result list to the caller (with
           ``Exception`` instances in the slots that failed).
        5. Emit DIAG-level summary with total duration + success
           count.

        Args:
            event: ``Events`` enum value or string equivalent.
            **payload: forwarded to each handler as kwargs.

        Returns:
            List of per-handler results (or ``Exception``
            instances for failed handlers), in registration
            order. Empty list if no subscribers.
        """
        key = self._key(event)
        handlers = list(self._handlers.get(key, []))
        if not handlers:
            logger.debug("[DIAG] event=%s handlers=0", key)
            return []
        started = time.monotonic()
        logger.debug(
            "[DIAG] event=%s handlers=%d payload_keys=%s",
            key,
            len(handlers),
            list(payload.keys()),
        )
        tasks = [self._invoke(h, payload) for h in handlers]
        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )
        once_list = self._once.get(key, [])
        if once_list:
            remaining = [h for h in self._handlers[key] if h not in once_list]
            self._handlers[key] = remaining
            self._once[key] = []
        dt_total = (time.monotonic() - started) * 1000
        ok = sum(1 for r in results if not isinstance(r, Exception))
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(
                    "[EventBus] handler #%d for %s failed: %s: %s",
                    i,
                    key,
                    type(r).__name__,
                    r,
                )
        logger.debug(
            "[DIAG] event=%s total=%.2fms success=%d/%d",
            key,
            dt_total,
            ok,
            len(results),
        )
        return results

    async def _invoke(self, handler: Handler, payload: dict[str, Any]) -> Any:
        """Dispatch one handler call, awaiting it directly or on a thread.

        ``inspect.iscoroutinefunction`` checks at call time
        because handlers might be wrapped (e.g. by the dev-ex
        decorators) — the check via the function attribute
        ``__call__`` would miss those cases.

        Args:
            handler: the callable to invoke.
            payload: kwargs to forward.

        Returns:
            The handler's return value (after await for async
            handlers, after thread completion for sync ones).
        """
        if inspect.iscoroutinefunction(handler):
            return await handler(**payload)
        return await asyncio.to_thread(handler, **payload)

    @staticmethod
    def _key(event: Events | str) -> str:
        """Normalise event identifiers to a canonical string key.

        ``Events`` enum members are mapped to their ``.value``;
        anything else is coerced via ``str()``. Used by every
        public method that accepts an event so internal storage
        is consistent (single string-keyed dict).

        Args:
            event: ``Events`` enum value or any string.

        Returns:
            The canonical string form used as the dict key.
        """
        if isinstance(event, Events):
            return event.value
        return str(event)
