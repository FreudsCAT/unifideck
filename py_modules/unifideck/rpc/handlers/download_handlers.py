"""DownloadHandlers — read/mutate the download queue from the frontend.

OP-25c | py_modules/unifideck/rpc/handlers/download_handlers.py

Thin RPC facade over ``DownloadService``: the frontend's
downloads tab calls these methods to render the queue, cancel
pending items, and (future) pick installation locations.

The download service is optional in the container (some test
boots opt it out), so every method uses ``_require`` to surface
a typed ``service_unavailable`` error if it's missing.
"""

from __future__ import annotations

from typing import Any, cast

from unifideck.rpc.handlers.base import RpcHandlerBase


class DownloadHandlers(RpcHandlerBase):
    """Download-queue read + cancel RPC methods."""

    async def cancel_download(self, store: str, game_id: str) -> Any:
        """Remove a queued download or stop a running one.

        Delegates to ``DownloadService.cancel`` which itself
        currently only handles the queued case (running
        downloads must finish their current subprocess).

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            ``Result`` dict from the download service:
            ``{success, error}`` with ``error="not_in_queue"``
            when the item couldn't be located.
        """
        svc = self._require(self._services.download, "download")
        return cast(dict, await svc.cancel(store, game_id))

    async def get_download_queue(self) -> Any:
        """Return a snapshot of the download queue + running items.

        Synchronous on the service side (no await) — the
        underlying ``get_queue`` builds the dict in-memory
        without any I/O.

        Returns:
            ``{"queued": [...], "running": [...]}`` with one
            entry per item (dict form from
            ``DownloadItem.to_dict``).
        """
        svc = self._require(self._services.download, "download")
        return cast(dict, svc.get_queue())

    async def get_storage_locations(self) -> Any:
        """Return the list of available install locations (placeholder).

        Currently a stub returning ``[]``; future work will
        enumerate the system's mounted drives (Steam Deck
        internal eMMC, microSD card, etc.) and report free
        space for each so the frontend can render a picker.

        Returns:
            Empty list.
        """
        return []
