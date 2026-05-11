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
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...stores import StoreRegistry
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
    # Future: "gog": (("_cloud_save", "cloudsave"), ...)
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
        try:
            store_instance = registry.get(store_id)
        except KeyError:
            continue
        except Exception as e:
            logger.warning(
                "[StoreInjector] failed to retrieve %s from registry: %s",
                store_id, e,
            )
            continue

        if store_instance is None:
            continue

        for store_attr, container_attr in injections:
            service_instance = getattr(container, container_attr, None)

            if service_instance is None:
                logger.warning(
                    "[StoreInjector] %s missing required service %s (feature disabled)",
                    store_id, container_attr,
                )
                continue

            try:
                setattr(store_instance, store_attr, service_instance)
                logger.debug(
                    "[StoreInjector] wired %s.%s = %s",
                    store_id, store_attr, container_attr,
                )
            except Exception as e:
                logger.warning(
                    "[StoreInjector] failed to inject %s into %s: %s",
                    container_attr, store_id, e,
                )
