"""event_bus/event_bus_extensions.py — Dead letter, filtering, typed registry.

# OP-09e | event_bus/event_bus_extensions.py | Depends: OP-05
"""
from __future__ import annotations
from typing import Any


class DeadLetterQueue:
    """Capture and inspect handler failures for debugging."""

    def __init__(self, max_size: int = 100) -> None:
        raise NotImplementedError("OP-09e")

    def record(self, event: str, handler_name: str, exc: Exception, payload: dict) -> None:
        raise NotImplementedError("OP-09e")

    def snapshot(self) -> list[dict[str, Any]]:
        raise NotImplementedError("OP-09e")

    def clear(self) -> None:
        raise NotImplementedError("OP-09e")


class PredicateFilter:
    """Wrap a handler with a predicate — only fire if predicate(payload) is True."""

    def __init__(self, handler, predicate) -> None:
        raise NotImplementedError("OP-09e")

    async def __call__(self, **kwargs) -> None:
        raise NotImplementedError("OP-09e")


class TypedEventRegistry:
    """Map event types to expected payload dataclasses for validation."""

    def register(self, event: str, payload_class: type) -> None:
        raise NotImplementedError("OP-09e")

    def validate(self, event: str, payload: dict) -> bool:
        raise NotImplementedError("OP-09e")


class EventSchema:
    """Declarative schema for an event payload."""

    def __init__(self, event: str, fields: dict[str, type]) -> None:
        raise NotImplementedError("OP-09e")

    def validate(self, payload: dict) -> list[str]:
        raise NotImplementedError("OP-09e")


class DebugSnapshot:
    """Point-in-time snapshot of bus state for diagnostics."""

    @staticmethod
    def capture(bus) -> dict[str, Any]:
        raise NotImplementedError("OP-09e")
