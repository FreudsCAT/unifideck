"""services/bootstrap/store_injector.py — Post-discovery store DI.

Cross-service dependency injection that runs AFTER both
``auto_discover`` (instantiates stores with minimal signature)
and ``bootstrap_services`` (builds the container). Walks
``_STORE_INJECTIONS`` and ``setattr``'s each mapping onto the
live store instance.

Rationale for late injection: ``auto_discover`` is a generic
scanner that knows nothing of store-specific Layer-5 deps.
Rather than expand its signature to accept every possible kwarg,
we keep ``auto_discover`` uniform and do specialised wiring here.

Refactor history (2026-05-14): ``inject_store_dependencies`` was
a single function at CC=16 — a double ``for store / for mapping``
loop with three separate failure paths (``registry.get`` raising
``KeyError``, raising another exception, or returning ``None``),
plus another failure path on ``setattr`` (``__slots__`` /
frozen). Split into two helpers so the main loop reads as
"for each store, look up, then wire each mapping".
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.stores import StoreRegistry

    from .container import ServiceContainer

logger = logging.getLogger(__name__)

# Post-construction wiring table.
# Each entry: store_id → tuple of (store_attr, container_attr).
# The assignment is conditional: None container slot (service
# failed to instantiate) leaves the store attribute at its
# constructor default, disabling the feature with a WARNING.
_STORE_INJECTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "amazon": (
        ("_browser_monitor", "browser_monitor"),
        ("_shortcut_service", "shortcut"),
        ("_edge", "edge_browser"),
    ),
    "epic": (
        ("_browser_monitor", "browser_monitor"),
        ("_shortcut_service", "shortcut"),
        ("_edge", "edge_browser"),
    ),
    "gog": (
        ("_browser_monitor", "browser_monitor"),
        ("_shortcut_service", "shortcut"),
        ("_edge", "edge_browser"),
    ),
    "microsoft": (
        ("_browser_monitor", "browser_monitor"),
        ("_shortcut_service", "shortcut"),
        ("_edge", "edge_browser"),
        ("_subscription_service", "microsoft_subscription"),
    ),
    "ubisoft": (
        ("_shortcut_service", "shortcut"),
    ),
}


def inject_store_dependencies(
    registry: StoreRegistry | None,
    container: ServiceContainer,
) -> None:
    """Inject Layer-5 service refs into already-registered stores.

    Called post-``auto_discover`` + post-``bootstrap_services``.
    Walks ``_STORE_INJECTIONS`` and ``setattr``'s each mapping.

    Failures are isolated per-store: ``registry.get`` raising,
    returning None, missing container slot, or setattr raising
    (``__slots__`` / frozen dataclass) all leave the other stores
    unaffected with the feature inactive for that store.

    ``registry=None`` → silent no-op (test harness).
    """
    if registry is None:
        return

    for store_id, injections in _STORE_INJECTIONS.items():
        store_instance = _resolve_store(registry, store_id)
        if store_instance is None:
            continue
        for store_attr, container_attr in injections:
            _inject_one(
                store_instance, store_id, store_attr,
                container_attr, container,
            )
            continue
        if store is None:
            continue
        for store_attr, container_attr in mappings:
            svc = getattr(container, container_attr, None)
            if svc is None:
                logger.info(
                    "[bootstrap] %s.%s not injected (container.%s is None)",
                    store_id,
                    store_attr,
                    container_attr,
                )
                continue
            try:
                setattr(store, store_attr, svc)
                logger.info(
                    "[bootstrap] injected %s.%s ← container.%s",
                    store_id,
                    store_attr,
                    container_attr,
                )
            except Exception as e:
                logger.warning(
                    "[bootstrap] failed to inject %s.%s: %s",
                    store_id,
                    store_attr,
                    e,
                )
        # Stores may need to (re)build their auth orchestrator
        # now that the browser monitor is wired. Stores that
        # implement this hook construct/refresh ``self._auth``
        # using their newly-set ``_browser_monitor``.
        rebuild = getattr(store, "_rebuild_auth_after_injection", None)
        if callable(rebuild):
            try:
                rebuild()
                logger.info(
                    "[bootstrap] %s auth rebuilt after injection",
                    store_id,
                )
            except Exception as e:
                logger.warning(
                    "[bootstrap] %s auth rebuild failed: %s",
                    store_id, e,
                )
