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

from collections.abc import Awaitable, Callable
from typing import Any

from ..core.types import Events

Handler = Callable[..., Awaitable[Any]] | Callable[..., Any]


class EventBus:
    """Pub/sub bus with async emission + error isolation."""

    def __init__(self) -> None:
        """Init two empty handler dicts: one for persistent
        subscriptions, one tracking one-shot handlers for removal.
        Keys are event *string values* so the bus survives module
        reloads that would break enum identity.
        """
        raise NotImplementedError("OP-09a: init _handlers and _oneshot dicts")

    def on(self, event: Events | str, handler: Handler) -> None:
        """Register a persistent handler. Called on every emission
        until removed via ``off()``.
        """
        raise NotImplementedError("OP-09a: append handler to _handlers[key]")

    def once(self, event: Events | str, handler: Handler) -> None:
        """Register a one-shot handler. Auto-removed after the
        next emission. Useful for awaiting a single completion
        (one sync cycle, one auth flow).
        """
        raise NotImplementedError("OP-09a: register + mark for auto-removal")

    def off(self, event: Events | str, handler: Handler) -> bool:
        """Unregister ``handler`` from ``event``. Return True if
        removed, False if not found. Safe on unknown handlers.
        """
        raise NotImplementedError("OP-09a: remove from _handlers, return found bool")

    def clear(self, event: Events | None = None) -> None:
        """Remove all handlers for ``event``, or all events if None.
        Used on shutdown and in tests.
        """
        raise NotImplementedError("OP-09a: clear specific event or all")

    def handler_count(self, event: Events | str) -> int:
        """Return number of handlers currently registered for ``event``."""
        raise NotImplementedError("OP-09a: len(_handlers.get(key, []))")

    async def emit(self, event: Events | str, **payload: Any) -> list[Any]:
        """Emit ``event`` to all registered handlers.

        Runs handlers concurrently via ``asyncio.gather(..., return_exceptions=True)``.
        Returns results in registration order — exceptions replace
        results for failed handlers. One-shot handlers are removed
        AFTER emission so they can't fire twice if re-emitted inside
        a handler. Logs per-handler timing and success count at DEBUG.
        """
        raise NotImplementedError("OP-09a: gather handlers, remove oneshots, log")

    async def _invoke(
        self, handler: Handler, payload: dict[str, Any],
    ) -> Any:
        """Call one handler with the payload.
        Async handlers are awaited directly. Sync handlers are
        offloaded via ``asyncio.to_thread`` so blocking I/O never
        freezes the event loop.
        """
        raise NotImplementedError("OP-09a: await if coroutine, else to_thread")

    @staticmethod
    def _key(event: Events | str) -> str:
        """Normalise an event reference to its string value.
        Accepts both ``Events.FOO`` and the raw ``'"foo"'`` so legacy
        callers passing strings keep working.
        """
        raise NotImplementedError("OP-09a: return str(event) / event.value")
