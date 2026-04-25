"""event_bus/event_bus_devex.py — Developer experience helpers.

# OP-09h | event_bus/event_bus_devex.py | Depends: (none)
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .event_bus import EventBus

_SUBSCRIBE_ATTR = "_subscribe_event"


class SchemaExtractor:
    """Extract event schemas from type annotations for docs/validation."""

    @staticmethod
    def extract(bus: EventBus) -> dict[str, Any]:
        """Return {event: [handler_qualnames]} for all registered handlers."""
        handlers = getattr(bus, "_handlers", {})
        return {
            event: [getattr(h, "__qualname__", str(h)) for h in hs]
            for event, hs in handlers.items()
        }


def subscribe(event: Any) -> Any:
    """Decorator: register a method as an event handler on a class.

    Usage::

        class MyService:
            @subscribe(Events.GAME_INSTALLED)
            async def on_game_installed(self, **kwargs):
                ...

    The decorated method is tagged with ``_subscribe_event`` so
    ``auto_wire`` can find and register it automatically.
    """
    def decorator(fn: Any) -> Any:
        setattr(fn, _SUBSCRIBE_ATTR, event)
        return fn
    return decorator


def auto_wire(bus: EventBus, instance: Any) -> None:
    """Auto-wire all @subscribe-decorated methods on ``instance`` to ``bus``.

    Introspects the instance for methods with ``_subscribe_event`` attribute
    and calls ``bus.on(event, bound_method)`` for each.
    """
    for name in dir(instance):
        if name.startswith("__"):
            continue
        try:
            method = getattr(instance, name)
        except AttributeError:
            continue
        event = getattr(method, _SUBSCRIBE_ATTR, None)
        if event is not None:
            bus.on(event, method)
