"""event_bus/event_bus_devex.py — Developer experience helpers.
# OP-09h | Depends: (none)
"""
from __future__ import annotations
from typing import Any


class SchemaExtractor:
    """Extract event schemas from type annotations for docs/validation."""

    @staticmethod
    def extract(bus) -> dict[str, Any]:
        raise NotImplementedError("OP-09h")


def subscribe(event):
    """Decorator: register a method as an event handler on a class."""
    raise NotImplementedError("OP-09h")


def auto_wire(bus, instance) -> None:
    """Auto-wire all @subscribe-decorated methods on ``instance`` to ``bus``."""
    raise NotImplementedError("OP-09h")
