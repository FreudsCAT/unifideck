"""services/bootstrap/constructor.py — Public service-construction entry points.

Two functions walking ``_SERVICE_DEFS`` via ``_instantiate_service``,
differing in **scope** and **dependency availability**:
- ``bootstrap_services()`` — full plugin path. Every service in
  the table attempted; each failure isolated on the container.
- ``build_service_subset()`` — reduced path for the out-of-process
  launcher. Only a named subset attempted; registry/cache/pipeline
  passed as None.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .container import ServiceContainer
from .paths import ServicePaths
from .service_defs import _SERVICE_DEFS, _instantiate_service

if TYPE_CHECKING:
    from collections.abc import Iterable

    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus.bus_pipeline import BusPipeline
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.stores import StoreRegistry

logger = logging.getLogger(__name__)


def bootstrap_services(
    bus: EventBus,
    registry: StoreRegistry,
    cache: CacheManager,
    config: ConfigManager,
    pipeline: BusPipeline,
) -> ServiceContainer:
    """Instantiate every Layer-5 service into a ServiceContainer.

    Each service created in an isolated try/except — one failure
    leaves that slot as None without aborting plugin boot
    (degraded mode). Failures logged at WARNING so production
    deployments see them in the Decky log.

    Must be called AFTER ``registry.auto_discover`` — some
    services subscribe to per-store events at construction time.
    """
    logger.info("[Bootstrap] resolving service paths from config")
    paths = ServicePaths.from_config(config)

    container = ServiceContainer()
    logger.info("[Bootstrap] instantiating %d Layer-5 services", len(_SERVICE_DEFS))

    for def_entry in _SERVICE_DEFS:
        attr = def_entry[0]
        try:
            instance = _instantiate_service(
                def_entry,
                bus=bus,
                registry=registry,
                cache=cache,
                config=config,
                paths=paths,
                pipeline=pipeline,
            )
            setattr(container, attr, instance)
        except Exception as e:
            logger.warning(
                "[Bootstrap] failed to instantiate service '%s': %s",
                attr, e,
            )

    # OAuthBrowserMonitor depends on the just-built `cdp` client.
    # Service-defs lambdas only see (bus, registry, cache, config,
    # paths, pipeline) — no partial-container access — so we
    # Edge CDP port — resolved once and shared between the
    # EdgeBrowser (launcher) and OAuthBrowserMonitor (redirect
    # capture). The monitor polls this port so it can see the
    # OAuth tabs that the launcher opens on the same port.
    cdp_port = 9222
    try:
        cdp_port = int(config.get("edge.cdp_port", 9222))
    except Exception:
        pass

    # construct it here in the post-loop step instead of adding a
    # special case to `_instantiate_service`. Quiet on `None` cdp
    # so a missing dependency doesn't block the rest of the boot.
    try:
        if container.cdp is not None:
            from ...auth.browser import OAuthBrowserMonitor
            container.browser_monitor = OAuthBrowserMonitor(
                cdp_client=container.cdp, config=config,
                edge_cdp_port=cdp_port,
            )
            logger.debug("[bootstrap] browser_monitor wired")
        else:
            logger.info(
                "[bootstrap] browser_monitor skipped — no cdp client",
            )
    except Exception as e:
        logger.warning(
            "[bootstrap] failed to wire browser_monitor: %s", e,
        )

    # EdgeBrowser — flatpak install + CDP launcher for OAuth
    # flows. The PDF spec lists it under ``auth/edge_browser/``
    # but never wires it into a service ; we instantiate it
    # here so the injector can hand a single shared instance
    # to every OAuth store.
    try:
        from ...auth.edge_browser import EdgeBrowser
        container.edge_browser = EdgeBrowser(
            cdp_port=cdp_port,
            locale_fn=lambda: str(
                config.get("ui.locale", "en-US"),
            ),
        )
        logger.info("[bootstrap] edge_browser wired")
    except Exception as e:
        logger.warning(
            "[bootstrap] failed to wire edge_browser: %s", e,
        )
    return container


def build_service_subset(
    bus: EventBus,
    config: ConfigManager,
    services: Iterable[str],
) -> ServiceContainer:
    """Construct a named subset of Layer-5 services.

    Used by ``launcher/bootstrap.py`` for the out-of-process
    launcher's reduced graph (shortcut, proton, cloudsave,
    launch_history typically). Registry / cache / pipeline passed
    as None to ``_instantiate_service``; services whose lambdas
    dereference those components will fail — caller's
    responsibility to only request compatible services.

    Unknown service names are logged + skipped.
    """
    paths = ServicePaths.from_config(config)
    container = ServiceContainer()

    # Map requested names to their definition row
    def_map = {row[0]: row for row in _SERVICE_DEFS}

    for name in services:
        if name not in def_map:
            logger.warning("[BootstrapSubset] unknown service requested: %s", name)
            continue

        try:
            instance = _instantiate_service(
                def_map[name],
                bus=bus,
                registry=None,
                cache=None,
                config=config,
                paths=paths,
                pipeline=None,
            )
            setattr(container, name, instance)
        except Exception as e:
            logger.warning(
                "[BootstrapSubset] failed to instantiate service '%s': %s",
                name, e,
            )

    return container
