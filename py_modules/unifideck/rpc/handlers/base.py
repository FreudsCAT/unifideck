"""Common base class for every RPC handler group.

OP-25a | py_modules/unifideck/rpc/handlers/base.py

``RpcHandlerBase`` declares the dependency-injection slots
every handler class needs: bus, store registry, cache manager,
config, sync service, and the typed service container. Concrete
handler groups (``ActionHandlers``, ``StoreHandlers``, etc.)
subclass it and use ``self._bus`` / ``self._registry`` / etc.

Two utility methods:

* ``_require(svc, name)`` — null-check helper that raises
  a typed ``service_unavailable`` error when a service slot
  is ``None`` (some services may be opted out via config or
  failed to construct).
* ``handler_methods()``  — introspect the instance for its
  public coroutine names, used by ``composer.bind_handlers``
  to copy them onto the plugin instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from unifideck.rpc.wrapper import RpcError

if TYPE_CHECKING:
    from unifideck.config.config_manager import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.core.sync_service import SyncService
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.services.bootstrap import ServiceContainer
    from unifideck.stores.shared.store_registry import StoreRegistry

T = TypeVar("T")


class RpcHandlerBase:
    """Common dependency container + utility methods for RPC handler groups."""

    def __init__(
        self,
        bus: EventBus,
        registry: StoreRegistry,
        cache: CacheManager,
        config: ConfigManager,
        sync_service: SyncService,
        services: ServiceContainer,
    ) -> None:
        """Store every injected dependency as a private attribute.

        No work happens here beyond attribute assignment —
        handler groups are pure facades over the injected
        collaborators. Construction is therefore cheap and
        side-effect-free, which keeps the composer's bootstrap
        sequence predictable.

        Args:
            bus: live event bus, used for emitting state-change
                events from handler methods.
            registry: store registry, accessed for per-store
                operations (auth, library, install, etc.).
            cache: shared cache manager, available for
                handlers that want to bypass the per-store
                cache layer.
            config: live config manager.
            sync_service: orchestrator for library-sync work.
            services: typed container holding the Layer-5
                services (shortcut, artwork, cloudsave,
                playtime, security, etc.).
        """
        self._bus = bus
        self._registry = registry
        self._cache = cache
        self._config = config
        self._sync = sync_service
        self._services = services

    @staticmethod
    def _require(svc: T | None, name: str) -> T:
        """Return ``svc`` if present, else raise ``RpcError("service_unavailable")``.

        Used by handler methods that depend on an optional
        service slot in the container. Centralising the
        ``None`` check here keeps individual handlers terse
        and produces a consistent error code/payload across
        the codebase.

        Args:
            svc: the optional service reference.
            name: identifier surfaced in the error context
                (matches the container attribute name —
                ``"shortcut"``, ``"cloudsave"``, etc.).

        Returns:
            ``svc`` itself, typed as ``T`` (the ``Optional``
            wrapper is stripped for the caller).

        Raises:
            RpcError: ``code="service_unavailable"``,
                context contains ``service=name``.
        """
        if svc is None:
            raise RpcError("service_unavailable", service=name)
        return svc

    def handler_methods(self) -> list[str]:
        """List the public coroutine method names this handler group exposes.

        Used by ``composer.bind_handlers`` to discover which
        methods to copy onto the plugin instance. Filters:

        * private (leading underscore) → skipped;
        * non-callable attributes → skipped;
        * the ``handler_methods`` method itself → skipped
          (we never expose introspection).

        The order is whatever ``dir(self)`` returns
        (alphabetical for typical CPython); the caller treats
        the list as a set.

        Returns:
            List of method-name strings.
        """
        return [
            name
            for name in dir(self)
            if not name.startswith("_")
            and callable(getattr(self, name))
            and name != "handler_methods"
        ]
