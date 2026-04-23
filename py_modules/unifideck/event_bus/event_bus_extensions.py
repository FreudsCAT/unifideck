"""event_bus/event_bus_extensions.py — Dead letter, filtering, typed registry.

# OP-09e | event_bus/event_bus_extensions.py | Depends: OP-05
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


class DeadLetterQueue:
    """Capture and inspect handler failures for debugging."""

    def __init__(self, max_size: int = 100) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_size)

    def record(self, event: str, handler_name: str, exc: Exception, payload: dict) -> None:
        self._entries.appendleft({
            "event": event,
            "handler": handler_name,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "payload_keys": list(payload.keys()),
        })

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


class PredicateFilter:
    """Wrap a handler with a predicate — only fire if predicate(payload) is True."""

    def __init__(self, handler: Any, predicate: Any) -> None:
        self._handler = handler
        self._predicate = predicate
        self.__qualname__ = getattr(handler, "__qualname__", str(handler))

    async def __call__(self, **kwargs: Any) -> Any:
        if self._predicate(kwargs):
            import asyncio
            if asyncio.iscoroutinefunction(self._handler):
                return await self._handler(**kwargs)
            return self._handler(**kwargs)
        return None


class TypedEventRegistry:
    """Map event types to expected payload dataclasses for validation."""

    def __init__(self) -> None:
        self._schemas: dict[str, type] = {}

    def register(self, event: str, payload_class: type) -> None:
        self._schemas[event] = payload_class

    def validate(self, event: str, payload: dict) -> bool:
        cls = self._schemas.get(event)
        if cls is None:
            return True  # no schema = anything goes
        import dataclasses
        if dataclasses.is_dataclass(cls):
            required = {f.name for f in dataclasses.fields(cls) if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING}
            return required.issubset(payload.keys())
        return True


@dataclass
class EventSchema:
    """Declarative schema for an event payload."""
    event: str
    fields: dict[str, type] = field(default_factory=dict)

    def validate(self, payload: dict) -> list[str]:
        errors: list[str] = []
        for name, expected_type in self.fields.items():
            if name not in payload:
                errors.append(f"missing field: {name}")
            elif not isinstance(payload[name], expected_type):
                errors.append(f"{name}: expected {expected_type.__name__}, got {type(payload[name]).__name__}")
        return errors


class DebugSnapshot:
    """Point-in-time snapshot of bus state for diagnostics."""

    @staticmethod
    def capture(bus: Any) -> dict[str, Any]:
        handlers = getattr(bus, "_handlers", {})
        return {
            "registered_events": list(handlers.keys()),
            "handler_counts": {k: len(v) for k, v in handlers.items()},
            "total_handlers": sum(len(v) for v in handlers.values()),
            "oneshot_count": len(getattr(bus, "_oneshot", set())),
        }
