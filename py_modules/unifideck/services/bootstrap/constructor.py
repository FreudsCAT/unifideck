"""Plugin bootstrap orchestrator — single entry-point called from ``main.py``.

OP-13d | py_modules/unifideck/services/bootstrap/constructor.py

``bootstrap_services(plugin)`` is the function called by
``Plugin._main`` to wire up the entire Layer-5 graph. It :

1. builds the ``ServicePaths``;
2. iterates the service-definition table from ``service_defs``;
3. constructs each service in order (resolving dependencies from
   the partially-built container);
4. returns the populated ``ServiceContainer``.

``build_service_subset`` is the variant used by tests : it constructs
only a named subset of services to keep test boot time minimal.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from .container import ServiceContainer
from .paths import ServicePaths
from .service_defs import _SERVICE_DEFS, _instantiate_service

if TYPE_CHECKING:
    from collections.abc import Iterable
    from ...config import ConfigManager
    from ...core.cache_manager import CacheManager
    from ...event_bus.bus_pipeline import BusPipeline
    from ...event_bus.event_bus import EventBus
    from ...stores import StoreRegistry
logger = logging.getLogger(__name__)


def bootstrap_services(
    bus: EventBus,
    registry: StoreRegistry,
    cache: CacheManager,
    config: ConfigManager,
    pipeline: BusPipeline,
) -> ServiceContainer:
    """Construct every Layer-5 service and return a populated container.

    Walks ``_SERVICE_DEFS`` in declared order. For each entry it
    calls ``_instantiate_service`` to build the service and sets it
    on the container under the entry's attribute name.

    Per-service construction failures are caught and logged at WARN
    level rather than propagated — a single broken service must not
    block the entire plugin from booting. The corresponding
    container slot stays at ``None`` and downstream consumers are
    responsible for ``None``-checks (or graceful degradation).

    Args:
        bus: live event bus from the pipeline.
        registry: store registry (already populated by
            ``StoreRegistry.auto_discover``).
        cache: shared cache manager.
        config: live config manager.
        pipeline: composed bus pipeline (replay, watchdog, etc.).

    Returns:
        A ``ServiceContainer`` with one slot per ``_SERVICE_DEFS``
        entry, populated with the constructed service or ``None``
        on construction failure.
    """
    paths = ServicePaths.from_config(config)
    container = ServiceContainer()
    for def_entry in _SERVICE_DEFS:
        attr = def_entry[0]
        try:
            instance = _instantiate_service(
                def_entry,
                bus,
                registry,
                cache,
                config,
                paths,
                pipeline,
            )
            setattr(container, attr, instance)
            logger.debug(
                "[bootstrap] %s wired: %s.%s",
                attr,
                def_entry[1],
                def_entry[2],
            )
        except Exception as e:
            logger.warning(
                "[bootstrap] failed to wire %s (%s.%s): %s",
                attr,
                def_entry[1],
                def_entry[2],
                e,
            )
    return container


def build_service_subset(
    bus: EventBus,
    config: ConfigManager,
    paths: ServicePaths,
    attrs: Iterable[str],
) -> dict[str, Any]:
    """Build a named subset of services for testing.

    Iterates ``_SERVICE_DEFS`` but only constructs entries whose
    attribute names appear in ``attrs``. Unlike ``bootstrap_services``
    this variant skips the registry, cache and pipeline (passes
    ``None`` for each), so it can only build services that don't
    depend on those — typically the leaf services with minimal
    wiring.

    Failures are caught and recorded as ``None`` slots in the
    returned dict (rather than raising), matching the production
    bootstrap's tolerance policy.

    Args:
        bus: stub or real event bus for the test.
        config: stub or real config manager.
        paths: pre-built ``ServicePaths`` (the subset variant
            doesn't derive paths from config).
        attrs: iterable of service-attribute names to build (e.g.
            ``["metadata", "proton"]``).

    Returns:
        Mapping ``attr → service_instance | None`` for every
        requested attribute, in iteration order.
    """
    selected = set(attrs)
    services: dict[str, Any] = {}
    for def_entry in _SERVICE_DEFS:
        attr = def_entry[0]
        if attr not in selected:
            continue
        try:
            services[attr] = _instantiate_service(
                def_entry,
                bus,
                None,
                None,
                config,
                paths,
                None,
            )
            logger.debug(
                "[subset] %s wired: %s.%s",
                attr,
                def_entry[1],
                def_entry[2],
            )
        except Exception as e:
            logger.warning(
                "[subset] failed to wire %s (%s.%s): %s",
                attr,
                def_entry[1],
                def_entry[2],
                e,
            )
            services[attr] = None
    return services
