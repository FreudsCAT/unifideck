"""event_bus/event_bus_scaling.py — Batch dispatch for same-type events.

BatchDispatcher buffers same-type events for a short time window
and delivers them as a list to handlers that opt in via a
`supports_batch = True` class attribute plus an `on_batch()` method.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

DEFAULT_BATCH_WINDOW_MS = 50
DEFAULT_BATCH_MAX_SIZE = 100


class BatchDispatcher:
    """Buffer events for a short window before flushing as a list.

    Handlers opt in by declaring `supports_batch = True` + an
    `async def on_batch(self, events)` method. The dispatcher
    checks this via `handler_supports_batch()` and routes the
    call through batched delivery instead of per-event.

    Buffers flush on size threshold OR time window expiration.
    `flush_all()` is called at shutdown to avoid losing items.
    """

    def __init__(  # noqa: D107 — class docstring documents the constructor's contract
        self,
        *,
        window_ms: int = DEFAULT_BATCH_WINDOW_MS,
        max_size: int = DEFAULT_BATCH_MAX_SIZE,
    ) -> None:
        self._window_ms = window_ms
        self._max_size = max_size
        self._buffers: dict[str, list[Any]] = {}
        self._last_flush_ms: dict[str, float] = {}

    def add(self, key: str, item: Any) -> bool:
        """Append to the buffer. Returns True if flush is due."""
        buf = self._buffers.setdefault(key, [])
        buf.append(item)
        if len(buf) >= self._max_size:
            return True
        last = self._last_flush_ms.get(key)
        now_ms = time.monotonic() * 1000
        if last is None:
            self._last_flush_ms[key] = now_ms
            return False
        return (now_ms - last) >= self._window_ms

    def drain(self, key: str) -> list[Any]:
        """Remove and return the buffered items for `key`."""
        items = self._buffers.pop(key, [])
        self._last_flush_ms[key] = time.monotonic() * 1000
        return items

    def flush_all(self) -> dict[str, list[Any]]:
        """Drain every buffered key at shutdown."""
        out = {k: v for k, v in self._buffers.items() if v}
        self._buffers.clear()
        return out

    @staticmethod
    def handler_supports_batch(handler: Callable) -> bool:
        """True if the handler declares batch mode."""
        return (
            getattr(handler, "supports_batch", False) is True
            and callable(getattr(handler, "on_batch", None))
        )
