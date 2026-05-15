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
    "microsoft": (
        ("_subscription_service", "microsoft_subscription"),
    ),
    "ubisoft": (
        ("_shortcut_service", "shortcut"),
    ),
    "gog": (
        ("_browser_monitor", "oauth_browser_monitor"),
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


# ─────────────────────────────────────────────────────────────────
# Private helpers — extracted from a former single CC=16 function
# ─────────────────────────────────────────────────────────────────


def _resolve_store(registry: StoreRegistry, store_id: str) -> Any | None:
    """Look up ``store_id`` in the registry, collapsing all
    "not present" cases to ``None``.

    Three distinct registry behaviours map here to the same
    result (the caller doesn't care *why* the store is absent):

        * ``KeyError`` — the canonical "unknown id" shape;
          silent skip (this store simply isn't enabled here).
        * Any other exception — registry is sick (corrupt
          state, partial init); WARN and skip so the other
          stores can still be wired.
        * Returned ``None`` — registry signals "the store id
          is known but the instance is unavailable" (e.g. its
          constructor blew up); silent skip, the constructor
          path has already logged.
    """
    try:
        instance = registry.get(store_id)
    except KeyError:
        return None
    except Exception as err:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
        logger.warning(
            "[StoreInjector] failed to retrieve %s from registry: %s",
            store_id, err,
        )
        return None
    return instance


def _inject_one(
    store_instance: Any,
    store_id: str,
    store_attr: str,
    container_attr: str,
    container: ServiceContainer,
) -> None:
    """Wire one ``(store_attr, container_attr)`` mapping onto a store.

    Two failure paths, each isolated to this one mapping (other
    mappings for the same store still run):

        * Missing container slot — the corresponding service
          failed to instantiate during ``bootstrap_services``;
          the store's constructor default takes over and the
          feature is silently disabled with a WARN.
        * ``setattr`` raises — the store class uses ``__slots__``
          or is a frozen dataclass without an "_injection"
          escape hatch; WARN and leave the store unchanged.
    """
    service_instance = getattr(container, container_attr, None)
    if service_instance is None:
        logger.warning(
            "[StoreInjector] %s missing required service %s "
            "(feature disabled)",
            store_id, container_attr,
        )
        return
    try:
        setattr(store_instance, store_attr, service_instance)
    except Exception as err:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
        logger.warning(
            "[StoreInjector] failed to inject %s into %s: %s",
            container_attr, store_id, err,
        )
        return
    logger.debug(
        "[StoreInjector] wired %s.%s = %s",
        store_id, store_attr, container_attr,
    )
