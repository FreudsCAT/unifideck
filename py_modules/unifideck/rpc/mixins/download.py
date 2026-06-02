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

import logging
import os
from pathlib import Path
from typing import Any

from unifideck.core.types.identifiers import (
    InvalidIdentifierError,
    validate_game_id,
    validate_store_id,
)
from unifideck.rpc.errors import RpcError

logger = logging.getLogger(__name__)


class DownloadRPCMixin:
    """Game install/uninstall, download queue, and storage locations."""

    registry: Any
    services: Any
    sync_service: Any

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

    async def install_game(self, store: str, game_id: str, options: Any = None, **kw: Any) -> Any:
        """Queue a game install via the download service.

        Accepts an optional ``options`` dict as a positional arg
        (the frontend passes ``{storage, language, title}``
        positionally through the RPC bridge). Resolves the
        ``storage`` type (``"internal"|"sdcard"|"custom"``) to a
        filesystem path, extracts the game title for the queue UI,
        and enqueues the install via ``DownloadService.add()``.

        Returns immediately with ``{success: true/false, error: ...}``
        — the actual install runs asynchronously through the
        download worker.
        """
        store, game_id = self._validate_pair(store, game_id)
        opts: dict[str, Any] = dict(kw)
        if isinstance(options, dict):
            opts.update(options)

        storage_type = opts.pop("storage", None)
        base_path = _resolve_storage_path(storage_type, getattr(self, "config", None))
        if not base_path:
            base_path = str(Path("~/Games").expanduser())

        title: str = opts.pop("title", "") or opts.pop("game_title", "")

        logger.info("[download] install_game store=%s game_id=%s storage=%s base_path=%s title=%s",
                     store, game_id, storage_type, base_path, title)

        download_svc = self._require_download()
        result = await download_svc.add(
            store=store,
            game_id=game_id,
            install_path=base_path,
            title=title,
            is_update=False,
        )
        return {"success": result.success, "error": result.error}

    async def update_game(self, app_id: int, **kw: Any) -> Any:
        """Queue an update for an already-installed game.

        Triggered by the Play→Update button, which only appears
        when ``check_game_update`` reported an available update.
        Resolves the Steam ``app_id`` back to its ``(store,
        game_id, install_path)`` via the sync layer, then enqueues
        with ``is_update=True`` so the worker dispatches to
        ``store.update_game`` and the UI labels it an update.
        """
        info = self.sync_service.get_game_info(app_id) if self.sync_service else None
        if not info:
            return {"success": False, "error": "game_not_found"}

        store, game_id = self._validate_pair(
            info.get("store", ""), info.get("store_game_id", ""),
        )
        title = info.get("title", "") or ""
        # The connectors re-resolve their own install path on update;
        # pass the known one when available, else the internal base
        # (same resolution install_game uses for "internal").
        install_path = (
            info.get("install_path")
            or _resolve_storage_path("internal", getattr(self, "config", None))
            or ""
        )

        logger.info("[download] update_game app_id=%s store=%s game_id=%s install_path=%s",
                     app_id, store, game_id, install_path)

        download_svc = self._require_download()
        result = await download_svc.add(
            store=store,
            game_id=game_id,
            install_path=install_path,
            title=title,
            is_update=True,
        )
        return {"success": result.success, "error": result.error}

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


# ─── Storage type → path resolution ───────────────────────────


# Filesystem types to skip when scanning for external mounts.
# tmpfs (e.g. /dev/shm), devtmpfs, proc, sysfs and other
# virtual filesystems should never be offered as install targets.
_SKIP_FSTYPES: frozenset[str] = frozenset({
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup",
    "cgroup2", "pstore", "bpf", "debugfs", "tracefs", "hugetlbfs",
    "ramfs", "overlay", "squashfs", "fuse.gvfsd-fuse",
    "fuse.portal", "securityfs", "configfs", "efivarfs",
    "autofs", "mqueue",
})


def _resolve_storage_path(storage_type: str | None, config: Any) -> str | None:
    """Convert a storage type string to a filesystem path.

    ``"internal"`` → ``~/Games``,
    ``"sdcard"``    → first external mount + ``/Games``,
    ``"custom"``    → ``download.custom_path`` from config,
    ``None``        → ``None`` (store connector uses its default).
    """
    if not storage_type:
        return None

    if storage_type == "internal":
        path = str(Path("~/Games").expanduser())
        logger.debug("[download] resolved internal → %s", path)
        return path

    if storage_type == "sdcard":
        home_dev = os.stat(str(Path.home())).st_dev
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    fstype = parts[2]
                    if fstype in _SKIP_FSTYPES:
                        continue
                    mp = parts[1]
                    try:
                        if os.stat(mp).st_dev == home_dev:
                            continue
                    except OSError:
                        continue
                    if not os.access(mp, os.W_OK):
                        continue
                    games_path = os.path.join(mp, "Games")
                    os.makedirs(games_path, exist_ok=True)
                    logger.debug("[download] resolved sdcard → %s (%s)", games_path, fstype)
                    return games_path
        except OSError as e:
            logger.warning("[download] sdcard resolution failed: %s", e)
        return None

    if storage_type == "custom":
        if config is not None:
            try:
                path = config.get("download.custom_path", None)
                if path:
                    logger.debug("[download] resolved custom → %s", path)
                    return path
            except Exception as e:
                logger.warning("[download] custom_path lookup failed: %s", e)
        return None

    logger.warning("[download] unknown storage type: %s", storage_type)
    return None
