"""Observability and developer experience utilities.

P8.8 TraceContext — propagates trace_id + parent_span_id through
event kwargs so you can reconstruct causal chains.
P8.9 EventRecorder — captures every emitted event to a JSONL
file for later replay. Opt-in via `start_recording()`.
Companion EventReplayer reads the file and re-injects.
P8.10 @subscribe decorator — declarative handler registration
at module import time, collected by SubscriptionRegistry.
P8.11 SchemaExtractor — static AST analysis of `bus.emit()`
calls to extract the set of kwargs per event type,
used to generate JSON Schema documentation.

"""
from __future__ import annotations

import ast
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
)

if TYPE_CHECKING:
    from ..core.types import Events
    from .event_bus import EventBus
    from .supervision.watchdog_handler import HandlerWatchdog

logger = logging.getLogger(__name__)


# ── P8.10 — @subscribe decorator + registry ─────────────────────

@dataclass
class _Subscription:
    event: str
    handler: Callable
    priority: int | None = None
    timeout: float | None = None
    scope: str | None = None


class SubscriptionRegistry:
    """Global-ish registry populated by the @subscribe decorator.

    A singleton at module level is dangerous for tests, so we
    expose both the class and a default instance. Tests can
    create fresh registries to avoid cross-contamination.
    """

    def __init__(self) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        self._subs: list[_Subscription] = []

    def add(self, sub: _Subscription) -> None:  # noqa: D102 — documentation pending (Sprint D)
        self._subs.append(sub)

    def all(self) -> list[_Subscription]:  # noqa: D102 — documentation pending (Sprint D)
        return list(self._subs)

    def apply(self, bus: EventBus) -> int:
        """Register every pending subscription on the bus."""
        count = 0
        for s in self._subs:
            if hasattr(bus, "on"):
                bus.on(s.event, s.handler)
                count += 1
        return count

    def clear(self) -> None:  # noqa: D102 — documentation pending (Sprint D)
        self._subs.clear()


# Module-level singleton for decorator use
default_registry = SubscriptionRegistry()


def subscribe(
    event: str | Events,
    *,
    priority: int | None = None,
    timeout: float | None = None,
    scope: str | None = None,
    registry: SubscriptionRegistry | None = None,
):
    """Decorator that registers a handler at import time OR at
    instance wiring time.

    Mode 1 — module-level function: registered immediately in
    the default_registry. Call registry.apply(bus) at startup.

    Mode 2 — instance method: metadata is attached to the
    function; auto_wire(instance, bus) in __init__ walks the
    instance and binds each method at runtime when `self` is
    available. See auto_wire() docstring for an example.
    """

    def decorator(fn: Callable) -> Callable:
        event_key = getattr(event, "value", event)
        # Dynamic attribute: @subscribe decorator attaches metadata
        # to the function so auto_wire() (or the module-level
        # registration below) can discover it. setattr/getattr
        # avoid a Protocol detour for one internal convention.
        meta = _Subscription(
            event=str(event_key),
            handler=fn,
            priority=priority,
            timeout=timeout,
            scope=scope,
        )
        setattr(fn, "__subscribe_meta__", meta)  # noqa: B010
        # Instance methods delayed to auto_wire time; free
        # functions registered immediately.
        if _looks_like_instance_method(fn):
            return fn
        reg = registry or default_registry
        reg.add(getattr(fn, "__subscribe_meta__"))  # noqa: B009
        return fn

    return decorator


def _looks_like_instance_method(fn: Callable) -> bool:
    """Heuristic: first positional arg is 'self' or 'cls'.

    Used to delay registration until auto_wire() is called with
    a real instance. Not foolproof (a free function could name
    its first arg 'self'), but matches PEP 8 conventions that
    every real codebase follows.
    """
    try:
        import inspect
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        return bool(params) and params[0] in ("self", "cls")
    except (TypeError, ValueError):
        return False


def auto_wire(
    instance: Any,
    bus: EventBus,
    *,
    registry: SubscriptionRegistry | None = None,
    watchdog: HandlerWatchdog | None = None,
) -> int:
    """Scan `instance` for @subscribe-decorated methods and wire them.

    Call from a service's `__init__` after the bus is stored:

        class MyService:
            def __init__(self, bus, watchdog=None):
                self._bus = bus
                auto_wire(self, bus, watchdog=watchdog)

    """
    count = 0
    for attr_name in dir(instance):
        if attr_name.startswith("__"):
            continue
        attr, meta = _resolve_subscribe_target(instance, attr_name)
        if meta is None:
            continue
        if not hasattr(bus, "on"):
            continue
        bus.on(meta.event, attr)
        count += 1
        if watchdog is not None:
            _register_with_watchdog(instance, attr_name, watchdog)
        if registry is not None:
            registry.add(
                _Subscription(
                    event=meta.event,
                    handler=attr,
                    priority=meta.priority,
                    timeout=meta.timeout,
                    scope=meta.scope,
                ),
            )
    return count


def _resolve_subscribe_target(instance: Any, attr_name: str):
    """Return (bound_attr, subscribe_meta) or (None, None)."""
    try:
        attr = getattr(instance, attr_name)
    except AttributeError:
        return None, None
    meta = getattr(attr, "__subscribe_meta__", None)
    if meta is None:
        func = getattr(attr, "__func__", None)
        if func is not None:
            meta = getattr(func, "__subscribe_meta__", None)
    return (attr if meta is not None else None), meta


def _register_with_watchdog(
    instance: Any, attr_name: str, watchdog: HandlerWatchdog,
) -> None:
    """Register the handler with the watchdog under its qualname.

    Best-effort: a failure to register never blocks bus wiring.
    """
    qualname = f"{type(instance).__name__}.{attr_name}"
    try:
        watchdog.register(qualname)
    except (AttributeError, RuntimeError) as e:
        logger.debug(
            "[event_bus_devex] watchdog register failed for %s: %s",
            qualname, e,
        )


# ── P8.11 — Static schema extraction ────────────────────────────

class SchemaExtractor:
    """Walk a Python source file and extract bus.emit() kwargs.

    Not called at runtime — this runs as part of a CI/codegen
    step to produce a JSON schema of all events a module emits.
    Helps catch typos in kwargs names before they reach runtime.
    """

    @staticmethod
    def extract_from_source(source: str) -> dict[str, set[str]]:
        """Return {event_value: {kwarg_names}} from `source`."""
        out: dict[str, set[str]] = {}
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return out
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not SchemaExtractor._is_emit_call(node):
                continue
            event_name = SchemaExtractor._extract_event_name(node)
            if event_name is None:
                continue
            kwarg_names = {
                kw.arg for kw in node.keywords if kw.arg is not None
            }
            out.setdefault(event_name, set()).update(kwarg_names)
        return out

    @staticmethod
    def _is_emit_call(node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "emit":
            return True
        return bool(isinstance(func, ast.Attribute) and func.attr == "enqueue")

    @staticmethod
    def _extract_event_name(node: ast.Call) -> str | None:
        if not node.args:
            return None
        first = node.args[0]
        # Either Events.X or "literal_string"
        if isinstance(first, ast.Attribute):
            return first.attr  # e.g. "GAME_LAUNCHED"
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None
