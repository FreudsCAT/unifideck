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
    """Bootstrap services."""
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
    """Build service subset."""
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
