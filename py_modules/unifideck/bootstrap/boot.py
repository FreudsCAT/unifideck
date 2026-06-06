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
from pathlib import Path
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
    inject_store_dependencies,
    start_async_services,
)
from unifideck.stores import StoreRegistry

logger = logging.getLogger(__name__)


async def boot_plugin(
    plugin: Any,
    *,
    decky_plugin_dir: str,
    decky_runtime_dir: str,
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
            as the plugin root. **Read-only on user installs.**
            Used to resolve ``defaults/`` and
            ``py_modules/unifideck/stores/``.
        decky_runtime_dir: Writable per-plugin runtime directory
            (``DECKY_PLUGIN_RUNTIME_DIR``). Holds the cache and any
            other state that needs to survive plugin reloads.
            Never use ``decky_plugin_dir`` for writable state — that
            location is owned by the install process and is
            read-only on normal user installs.
        user_config_path_resolver: Zero-arg callable that returns
            the user overrides JSON path. Injected so tests can
            stub out the XDG/env resolution without monkey-patching.

    Never raises: validation failures flag degraded mode and
    continue booting; service bootstrap failures are logged by
    the ServiceContainer itself and leave the failed service
    entry as ``None`` for the mixin guards to handle.
    """
    pipeline = await _boot_layer2_core(plugin, decky_runtime_dir)
    await _boot_config_and_validate(
        plugin, decky_plugin_dir, user_config_path_resolver,
    )
    _boot_layer4_stores(plugin, decky_plugin_dir)
    await _boot_layer5_services(plugin, pipeline, decky_plugin_dir)
    await _boot_updater(plugin, decky_plugin_dir)
    logger.info("[Unifideck] plugin loaded")


async def _boot_layer2_core(plugin: Any, decky_runtime_dir: str) -> Any:
    """Layer 2 — EventBus + pipeline + cache.

    Returns the ``BusPipeline`` so ``boot_plugin`` can forward it
    to ``bootstrap_services``.
    """
    plugin.bus = EventBus()
    pipeline = await build_eventbus_pipeline(plugin)
    plugin.cache = CacheManager(
        str(Path(decky_runtime_dir) / "cache"),
    )
    register_default_caches(plugin.cache)
    return pipeline


def _resolve_defaults_path(decky_plugin_dir: str) -> str:
    """Locate the bundled config.json across Decky build layouts.

    Two install layouts are valid in production:

    1. ``<plugin>/defaults/config.json`` — local builds via
       ``build-plugin.sh build_local`` and dev syncs that preserve
       the source directory layout.
    2. ``<plugin>/config.json`` — Decky CLI builds (``decky plugin
       build``). Decky CLI 0.0.8+ has a convention where the contents
       of ``defaults/`` get flattened to the install root on first
       install (so users can edit them, with the file preserved
       across plugin updates).

    We pick whichever exists, preferring the unflattened layout when
    both are present (more explicit). Returns the unflattened path
    even when neither exists — ConfigManager handles "missing
    defaults" by logging a warning and entering degraded mode, and
    paths.py has fallback defaults so boot still completes.
    """
    nested = str(Path(decky_plugin_dir) / "defaults" / "config.json")
    if Path(nested).is_file():
        return nested
    flattened = str(Path(decky_plugin_dir) / "config.json")
    if Path(flattened).is_file():
        return flattened
    return nested


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
    defaults_path = _resolve_defaults_path(decky_plugin_dir)
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
    # SyncService needs the launcher path so it can assign each
    # game a stable Steam-shortcut AppID (deterministic from
    # ``crc32(launcher_path + title)`` — survives install /
    # uninstall transitions). Without this, every game's
    # ``app_id`` stays at the per-store-default ``0`` and
    # downstream ShortcutService.reconcile + ArtworkService can't
    # key on it.
    launcher_path = str(
        Path(decky_plugin_dir) / "bin" / "unifideck-launcher",
    )
    plugin.sync_service = SyncService(
        plugin.registry, plugin.bus, launcher_path=launcher_path,
        config=plugin.config, cache=plugin.cache,
    )
    stores_dir = str(
        Path(decky_plugin_dir) / "py_modules" / "unifideck" / "stores",
    )
    plugin.registry.auto_discover(
        stores_dir,
        bus=plugin.bus,
        cache=plugin.cache,
        plugin_dir=decky_plugin_dir,
        config=plugin.config,
    )


async def _boot_layer5_services(
    plugin: Any, pipeline: Any, decky_plugin_dir: str,
) -> None:
    """Layer 5 — infrastructure services + async workers.

    Three phases :

    1. ``bootstrap_services`` builds the full service container
       (shortcut, download, cdp, browser_monitor, ...).
    2. ``inject_store_dependencies`` walks ``_STORE_INJECTIONS``
       (OP-13g) and writes each (attr, service) pair onto its
       auto-discovered store. Stores that expose
       ``_rebuild_auth_after_injection`` get it called so they
       can wire their auth flow against the freshly-injected
       ``_browser_monitor``.
    3. ``start_async_services`` kicks any background tasks
       (download worker, security audit pump, ...).
    """
    plugin.services = bootstrap_services(
        plugin.bus, plugin.registry, plugin.cache, plugin.config,
        pipeline, plugin_dir=decky_plugin_dir,
    )
    inject_store_dependencies(plugin.registry, plugin.services)
    # Post-bootstrap wiring: SyncService lives on ``plugin``, not on
    # the service container, so services that need to register
    # post-sync phases (currently CompatibilityService) get their
    # reference here. Without this call, ``mark_complete`` would
    # fire before the compat fetch finished.
    compat = getattr(plugin.services, "compatibility", None)
    if compat is not None:
        compat.wire_sync_service(plugin.sync_service)
    await start_async_services(plugin.services)


async def _boot_updater(plugin: Any, decky_plugin_dir: str) -> None:
    """Wire the self-updater service.

    The UpdaterService is lightweight and independent of the
    ServiceContainer — it only needs the EventBus and the path
    to ``package.json`` to read the installed version. Constructed
    separately so a failure here never blocks the rest of boot.

    Starts the 6-hour background polling task so the plugin
    can notify the frontend when a new version is available.
    """
    try:
        from unifideck.services.updater import UpdaterService

        package_json = str(Path(decky_plugin_dir) / "package.json")
        svc = UpdaterService(plugin.bus, package_json)
        plugin._updater_service = svc
        await svc.start_polling()
        logger.info("[Updater] service wired (v%s)", svc.get_current_version())
    except Exception:
        logger.exception("[Updater] failed to wire — update checking disabled")
        plugin._updater_service = None

