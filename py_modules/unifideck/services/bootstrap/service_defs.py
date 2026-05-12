"""Service definitions — table-driven service construction.

OP-13c | py_modules/unifideck/services/bootstrap/service_defs.py

This module declares the canonical list of services to construct at
boot and how to build each one. Each entry specifies :

* the service's name in the container (its attribute);
* the constructor (callable);
* the dependencies (other services / paths / bus) to pass in.

Centralising the definitions in a table (rather than spreading
``container.foo = FooService(...)`` calls across modules) makes the
service graph reviewable in one place and prevents the "where is this
constructed?" question.

``_instantiate_service`` is the helper that walks one entry, resolves
its dependencies from the partial container, and instantiates the
service.
"""

from __future__ import annotations
from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...core.cache_manager import CacheManager
    from ...event_bus.bus_pipeline import BusPipeline
    from ...event_bus.event_bus import EventBus
    from ...stores import StoreRegistry
    from .paths import ServicePaths
_SERVICE_DEFS: tuple[tuple, ...] = (
    (
        "shortcut",
        "unifideck.services.shortcut",
        "ShortcutService",
        lambda b, r, c, cfg, p, pl: (b, p.shortcuts_path, p.games_map_path),
        lambda b, r, c, cfg, p, pl: {},
    ),
    (
        "download",
        "unifideck.services.download",
        "DownloadService",
        lambda b, r, c, cfg, p, pl: (b, r, p.queue_file),
        lambda b, r, c, cfg, p, pl: {},
    ),
    (
        "metadata",
        "unifideck.services.metadata_service",
        "MetadataService",
        lambda b, r, c, cfg, p, pl: (b, c),
        lambda b, r, c, cfg, p, pl: {"config": cfg},
    ),
    (
        "artwork",
        "unifideck.services.artwork",
        "ArtworkService",
        lambda b, r, c, cfg, p, pl: (b, c, p.grid_dir),
        lambda b, r, c, cfg, p, pl: {"config": cfg},
    ),
    (
        "proton",
        "unifideck.services.proton_service",
        "ProtonService",
        lambda b, r, c, cfg, p, pl: (b, p.config_vdf_path),
        lambda b, r, c, cfg, p, pl: {},
    ),
    (
        "cdp",
        "unifideck.cdp.cdp_client",
        "CDPClient",
        lambda b, r, c, cfg, p, pl: (),
        lambda b, r, c, cfg, p, pl: {"config": cfg},
    ),
    (
        "cloudsave",
        "unifideck.services.cloud_save",
        "CloudSaveService",
        lambda b, r, c, cfg, p, pl: (b, p.local_save_root),
        lambda b, r, c, cfg, p, pl: {"cloud_root": p.cloud_root},
    ),
    (
        "metrics",
        "unifideck.core.metrics_collector",
        "MetricsCollector",
        lambda b, r, c, cfg, p, pl: (b,),
        lambda b, r, c, cfg, p, pl: {},
    ),
    (
        "account",
        "unifideck.services.account_service",
        "AccountService",
        lambda b, r, c, cfg, p, pl: (b, p.loginusers_path),
        lambda b, r, c, cfg, p, pl: {},
    ),
    (
        "playtime",
        "unifideck.services.playtime",
        "PlaytimeService",
        lambda b, r, c, cfg, p, pl: (b, p.playtime_db),
        lambda b, r, c, cfg, p, pl: {},
    ),
    (
        "feature_flags",
        "unifideck.services.feature_flag_service",
        "FeatureFlagService",
        lambda b, r, c, cfg, p, pl: (b,),
        lambda b, r, c, cfg, p, pl: {"config": cfg},
    ),
    (
        "probe_reaction",
        "unifideck.services.probe_reaction_service",
        "ProbeReactionService",
        lambda b, r, c, cfg, p, pl: (b, pl.watchdog),
        lambda b, r, c, cfg, p, pl: {"config": cfg},
    ),
    (
        "security",
        "unifideck.services.security",
        "SecurityService",
        lambda b, r, c, cfg, p, pl: (b,),
        lambda b, r, c, cfg, p, pl: {
            "config": cfg,
            "replay": pl.replay if pl else None,
        },
    ),
    (
        "launch_history",
        "unifideck.services.launch_history",
        "LaunchHistoryService",
        lambda b, r, c, cfg, p, pl: (cfg,),
        lambda b, r, c, cfg, p, pl: {"bus": b},
    ),
    (
        "microsoft_subscription",
        "unifideck.services.microsoft_subscription",
        "MicrosoftSubscriptionService",
        lambda b, r, c, cfg, p, pl: (b, c),
        lambda b, r, c, cfg, p, pl: {"config": cfg},
    ),
)


def _instantiate_service(
    def_entry: tuple,
    bus: EventBus,
    registry: StoreRegistry | None,
    cache: CacheManager | None,
    config: ConfigManager,
    paths: ServicePaths,
    pipeline: BusPipeline | None = None,
) -> Any:
    """Build one service from its entry in ``_SERVICE_DEFS``.

    Each entry is a 5-tuple ``(attr, module_path, class_name,
    build_args, build_kw)``. ``build_args`` and ``build_kw`` are
    lambdas that receive the six available dependencies (bus,
    registry, cache, config, paths, pipeline) and return the
    positional and keyword arguments to forward to the service's
    constructor.

    The lambdas encapsulate the per-service wiring rules in the
    table itself, so this helper stays generic: import the module,
    resolve the class, expand the lambdas, instantiate.

    Args:
        def_entry: one row of ``_SERVICE_DEFS`` describing how to
            construct a single service.
        bus: live event bus, threaded into every service.
        registry: store registry (some services need to enumerate
            stores; may be ``None`` in stripped-down test boots).
        cache: shared cache manager.
        config: live config manager.
        paths: derived filesystem paths.
        pipeline: composed bus pipeline (replay, watchdog, etc.) —
            optional, only some services consume it.

    Returns:
        The freshly-constructed service instance, ready to be
        stored on the ``ServiceContainer``.
    """
    _attr, module_path, class_name, build_args, build_kw = def_entry
    module = import_module(module_path)
    cls = getattr(module, class_name)
    args = build_args(bus, registry, cache, config, paths, pipeline)
    kwargs = build_kw(bus, registry, cache, config, paths, pipeline)
    return cls(*args, **kwargs)
