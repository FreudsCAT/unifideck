"""Post-discovery store wiring — injects services into auto-discovered stores.

OP-13g | py_modules/unifideck/services/bootstrap/store_injector.py

Stores are auto-discovered by ``StoreRegistry.auto_discover()`` after
the service container is built. By that point the services exist but
the freshly-instantiated stores don't have references to them yet —
they have ``None`` placeholders for fields like ``_shortcut_service``.

``inject_store_dependencies`` walks the ``_STORE_INJECTIONS`` table
(per-store list of ``(attr, service_name)`` pairs) and sets each
attribute on the corresponding store. The table is the canonical
source of "which services does store X need?".
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...stores import StoreRegistry
    from .container import ServiceContainer
logger = logging.getLogger(__name__)
_STORE_INJECTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "microsoft": (("_subscription_service", "microsoft_subscription"),),
}


def inject_store_dependencies(
    registry: StoreRegistry | None,
    container: ServiceContainer,
) -> None:
    """Inject store dependencies."""
    if registry is None:
        return
    for store_id, mappings in _STORE_INJECTIONS.items():
        try:
            store = registry.get(store_id)
        except Exception:
            logger.debug(
                "[bootstrap] registry.get(%r) raised, skipping injections",
                store_id,
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
