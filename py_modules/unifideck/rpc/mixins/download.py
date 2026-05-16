"""Download RPC mixin for Plugin class.

OP-26i | py_modules/unifideck/rpc/mixins/download.py

Mixin merging two slices that the handler groups split apart:

* per-game lifecycle (``install_game`` / ``uninstall_game`` /
  ``check_game_update``) — these live in ``StoreHandlers`` in
  the newer API;
* download-queue management (``cancel_download`` /
  ``get_download_queue``) — these live in ``DownloadHandlers``.

Storage-location RPCs (``get_storage_locations``,
``set_default_storage_location``, ``set_custom_install_path``)
live in a sibling ``StorageRPCMixin`` (OP-26j) so this file
keeps the 200 LOC ceiling.

Two private helpers centralise the null checks:

* ``_require_store`` — store-not-found errors;
* ``_require_download`` — download-service-unavailable errors.

``_validate_pair`` validates identifiers at the RPC boundary
so the rest of the codebase can treat them as already-sanitised.
"""
from __future__ import annotations

from typing import Any

from unifideck.core.types.identifiers import (
    InvalidIdentifierError,
    validate_game_id,
    validate_store_id,
)
from unifideck.rpc.errors import RpcError


class DownloadRPCMixin:
    """Game install/uninstall, download queue, and storage locations."""

    registry: Any
    services: Any

    @staticmethod
    def _validate_pair(store: str, game_id: str) -> tuple[str, str]:
        """Validate the ``(store, game_id)`` pair at the RPC boundary.

        Both identifiers flow into subprocess argv, filesystem
        paths, and URL templates downstream. Rejecting malformed
        values here means the rest of the codebase can treat them
        as already-sanitised. Raises ``RpcError("invalid_identifier")``
        on failure — the frontend gets a structured error, not a
        stack trace.

        Returns the pair unchanged so the call can be inlined:
        ``store, game_id = self._validate_pair(store, game_id)``.
        """
        try:
            return validate_store_id(store), validate_game_id(game_id)
        except InvalidIdentifierError as e:
            raise RpcError("invalid_identifier", reason=str(e)) from e

    def _require_store(self, store: str) -> Any:
        """Return store adapter or raise ``store_not_found``.

        Uses :meth:`StoreRegistry.get_store` (returns ``None`` on
        miss) rather than :meth:`get` (raises ``KeyError`` on
        miss). The previous code called ``get()`` and checked for
        ``None`` — that check never fired because ``get()`` raises
        instead of returning ``None``, so a missing store
        propagated a cryptic ``KeyError`` to the frontend instead
        of the documented ``store_not_found`` RPC error.
        """
        adapter = self.registry.get_store(store)
        if adapter is None:
            raise RpcError("store_not_found", store=store)
        return adapter

    def _require_download(self) -> Any:
        """Return download service or raise ``service_unavailable``."""
        svc = getattr(self.services, "download", None)
        if svc is None:
            raise RpcError("service_unavailable", service="download")
        return svc

    async def install_game(self, store: str, game_id: str, **kw: Any) -> Any:
        """Install a game via the responsible store connector.

        Real adapter method is ``install_game`` (an earlier
        version called ``.install`` which doesn't exist on any
        store, making install RPC raise ``AttributeError``).
        """
        store, game_id = self._validate_pair(store, game_id)
        return await self._require_store(store).install_game(game_id, **kw)

    async def uninstall_game(self, store: str, game_id: str) -> Any:
        """Uninstall a game via the responsible store connector.

        Real method is ``uninstall_game`` — see :meth:`install_game`.
        """
        store, game_id = self._validate_pair(store, game_id)
        return await self._require_store(store).uninstall_game(game_id)

    async def check_game_update(self, store: str, game_id: str) -> Any:
        """Check whether a specific game has an update available.

        :meth:`StoreBase.check_for_updates` is bulk (no args) and
        returns a ``list[str]`` of game ids with pending updates.
        Earlier this mixin called ``check_update(game_id)`` which
        matched neither the name nor the signature.
        """
        store, game_id = self._validate_pair(store, game_id)
        updatable = await self._require_store(store).check_for_updates()
        return {"has_update": game_id in (updatable or [])}

    async def cancel_download(self, store: str, game_id: str) -> Any:
        """Cancel an in-progress download.

        :meth:`DownloadService.cancel` takes ``(store, game_id)`` —
        the queue is keyed by ``"<store>:<game_id>"``. Earlier
        this mixin passed a single ``download_id`` which the
        service interpreted as ``store`` and silently failed to
        find any matching entry.
        """
        store, game_id = self._validate_pair(store, game_id)
        return await self._require_download().cancel(store, game_id)

    async def get_download_queue(self) -> Any:
        """Return the current download queue (sync method, no await)."""
        return self._require_download().get_queue()

    async def get_storage_locations(self) -> Any:
        """Return available storage locations."""
        storage = getattr(self.services, "storage", None)
        if storage is None:
            return []
        return await storage.get_locations()
