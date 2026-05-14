"""DownloadRPCMixin — install/uninstall/queue management RPC.

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

API note: ``cancel_download`` here takes a single
``download_id`` argument, whereas ``DownloadHandlers`` takes
``(store, game_id)`` — the mixin is on the older API where
downloads had their own opaque ids.
"""

from __future__ import annotations

from typing import Any

from unifideck.rpc import RpcError


class DownloadRPCMixin:
    """Install lifecycle + download-queue RPC for the older API."""

    registry: Any
    services: Any

    async def install_game(self, store: str, game_id: str, **kw: Any) -> Any:
        """Trigger a store-side install for a specific game.

        Per-store delegation: the registry resolves the
        store, then forwards to its ``install_game`` method.
        Typically enqueues the install rather than running
        it inline.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            **kw: install options (path, language, etc.)
                forwarded to the store.

        Returns:
            ``Result`` dict from the store's installer.

        Raises:
            RpcError: ``store_not_found`` (raised by
                ``_require_store``).
        """
        s = self._require_store(store)
        return await s.install_game(game_id, **kw)

    async def uninstall_game(self, store: str, game_id: str) -> Any:
        """Trigger a store-side uninstall for a specific game.

        Some stores can't actually uninstall (e.g. Game
        Pass streaming), in which case they return a typed
        error dict.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            ``Result`` dict from the store's uninstaller.

        Raises:
            RpcError: ``store_not_found``.
        """
        s = self._require_store(store)
        return await s.uninstall_game(game_id)

    async def check_game_update(self, store: str, game_id: str) -> Any:
        """Ask the store whether a game has an update available.

        Per-store implementations vary — some compare local
        manifests, others poll a version API.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            ``{has_update: bool, ...}`` dict, store-specific
            extras may include version strings or download
            size.

        Raises:
            RpcError: ``store_not_found``.
        """
        s = self._require_store(store)
        return await s.check_game_update(game_id)

    async def cancel_download(self, download_id: str) -> Any:
        """Cancel a queued or running download by opaque id.

        Older API where downloads had their own ids
        independent of (store, game_id). Delegates to
        ``DownloadService.cancel``.

        Args:
            download_id: opaque download identifier.

        Returns:
            ``Result`` dict from the download service.
        """
        download = self._require_download()
        return await download.cancel(download_id)

    async def get_download_queue(self) -> Any:
        """Return a snapshot of the download queue.

        Synchronous on the service side — the underlying
        ``get_queue`` builds the dict in-memory without I/O.

        Returns:
            Queue-state dict (shape determined by the
            download service).
        """
        download = self._require_download()
        return download.get_queue()

    def _require_store(self, store: str) -> Any:
        """Return the store from the registry or raise ``store_not_found``.

        The registry method here is ``get`` (older API) —
        the handler-group equivalent uses ``get_store``.

        Args:
            store: store identifier.

        Returns:
            The resolved store object.

        Raises:
            RpcError: ``code="store_not_found"`` with
                ``store=<id>`` context when the id is
                unknown.
        """
        s = self.registry.get(store)
        if s is None:
            raise RpcError("store_not_found", store=store)
        return s

    def _require_download(self) -> Any:
        """Return the download service or raise ``service_unavailable``.

        Returns:
            The ``DownloadService`` instance.

        Raises:
            RpcError: ``code="service_unavailable"``,
                ``service="download"`` when the service
                isn't wired.
        """
        if self.services.download is None:
            raise RpcError(
                "service_unavailable",
                service="download",
            )
        return self.services.download
