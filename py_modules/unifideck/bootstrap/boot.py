"""bootstrap.boot — full plugin cold-start orchestration.

Runs exactly once when Decky Loader loads Unifideck. The
ordering below is load-bearing:

  Layer 2 (core) → Layer 4 (stores) → Layer 5 (services)

Services subscribe to the EventBus in their ``__init__``, so the
event topology is only live after the bootstrap step.

Boot sequence (each step must complete before the next):

  1. ``EventBus`` instantiation — empty, no pipeline yet
  2. Pipeline construction — watchdog + latency + replay +
     batcher + dispatcher, with dispatcher.start() awaited
  3. ``CacheManager`` instantiation pointing at the data dir
  4. Cache name registration (``register_default_caches``) —
     MUST happen before stores are discovered because store
     constructors may call ``is_available()`` which reads
     from the cache
  5. ``ConfigManager`` with 3-layer merge (defaults + user + code)
  6. Config validation — marks plugin as degraded on failure but
     never prevents boot
  7. ``StoreRegistry`` + ``SyncService`` instantiation
  8. Store auto-discovery — scans ``stores/`` for connectors
  9. Layer-5 services bootstrap via ``ServiceContainer``
  10. ``start_async_services`` — kicks off long-lived service
      workers (cloudsave, download queue, etc.)

Mutates the plugin in place. Never raises.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from unifideck.bootstrap.cache_registry import register_default_caches
from unifideck.bootstrap.pipeline_factory import build_eventbus_pipeline
from unifideck.config import ConfigManager
from unifideck.config.startup import validate_config_at_startup
from unifideck.core.cache_manager import CacheManager
from unifideck.core.sync_service import SyncService
from unifideck.event_bus.event_bus import EventBus
from unifideck.services.bootstrap import (
    bootstrap_services,
    start_async_services,
)
from unifideck.stores import StoreRegistry

logger = logging.getLogger(__name__)


async def boot_plugin(
    plugin: Any,
    *,
    decky_plugin_dir: str,
    user_config_path_resolver: Any,
) -> None:
    """Cold-start ``plugin`` in place.

    Args:
        plugin: The ``Plugin`` instance. Will have its attributes
            populated in place — the method exists to preserve
            the subtle ordering of ``self.*`` assignments that
            services depend on (each new service may subscribe
            to events emitted by attributes set earlier).
        decky_plugin_dir: The absolute path passed by Decky Loader
            as the plugin root. Used to resolve ``defaults/``,
            ``data/``, and ``py_modules/unifideck/stores/``.
        user_config_path_resolver: Zero-arg callable that returns
            the user overrides JSON path. Injected so tests can
            stub out the XDG/env resolution without monkey-patching.

    Never raises: validation failures flag degraded mode and
    continue booting; service bootstrap failures are logged by
    the ServiceContainer itself and leave the failed service
    entry as ``None`` for the mixin guards to handle.
    """
    pipeline = await _boot_layer2_core(plugin, decky_plugin_dir)
    await _boot_config_and_validate(
        plugin, decky_plugin_dir, user_config_path_resolver,
    )
    _boot_layer4_stores(plugin, decky_plugin_dir)
    await _boot_layer5_services(plugin, pipeline)
    logger.info("[Unifideck] plugin loaded")


async def _boot_layer2_core(plugin: Any, decky_plugin_dir: str) -> Any:
    """Layer 2 — EventBus + pipeline + cache.

    Returns the ``BusPipeline`` so ``boot_plugin`` can forward it
    to ``bootstrap_services``.
    """
    plugin.bus = EventBus()
    pipeline = await build_eventbus_pipeline(plugin)
    plugin.cache = CacheManager(
        os.path.join(decky_plugin_dir, "data", "cache"),
    )
    register_default_caches(plugin.cache)
    return pipeline


async def _boot_config_and_validate(
    plugin: Any,
    decky_plugin_dir: str,
    user_config_path_resolver: Any,
) -> None:
    """Layer 3 — ConfigManager + startup validation.

    Validates the config at boot BEFORE stores are instantiated.
    Failures log a warning, flag the plugin as "degraded", emit
    CONFIG_VALIDATION_FAILED on the bus for SecurityService, and
    continue booting anyway so the user can still see the
    DiagnosticsPanel and fix their config. Validation covers
    user overrides as well.

    ConfigManager merges defaults/config.json + user overrides
    from the XDG location (~/.config/unifideck/config.json by
    default, overridable via UNIFIDECK_USER_CONFIG /
    XDG_CONFIG_HOME). The user file is allowed to be missing at
    first run: the manager skips the user layer and falls back
    to defaults + hardcoded values.
    """
    plugin._user_config_path = user_config_path_resolver()
    defaults_path = os.path.join(
        decky_plugin_dir, "defaults", "config.json",
    )
    plugin.config = ConfigManager(
        defaults_path=defaults_path,
        user_path=plugin._user_config_path,
    )
    (
        plugin._config_validation_result,
        plugin._config_degraded,
    ) = await validate_config_at_startup(
        bus=plugin.bus,
        config=plugin.config,
        defaults_path=defaults_path,
        user_config_path=plugin._user_config_path,
    )


def _boot_layer4_stores(plugin: Any, decky_plugin_dir: str) -> None:
    """Layer 4 — StoreRegistry + SyncService + auto-discovery."""
    plugin.registry = StoreRegistry(plugin.bus)
    plugin.sync_service = SyncService(plugin.bus, plugin.registry)
    stores_dir = os.path.join(
        decky_plugin_dir, "py_modules", "unifideck", "stores",
    )
    plugin.registry.auto_discover(
        stores_dir,
        bus=plugin.bus,
        cache=plugin.cache,
        plugin_dir=decky_plugin_dir,
        config=plugin.config,
    )


async def _boot_layer5_services(plugin: Any, pipeline: Any) -> None:
    """Layer 5 — infrastructure services + async workers."""
    plugin.services = bootstrap_services(
        plugin.bus, plugin.registry, plugin.cache, plugin.config,
        pipeline,
    )
    await start_async_services(plugin.services)
