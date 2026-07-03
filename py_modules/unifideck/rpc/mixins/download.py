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

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from unifideck.core import marker_sweep
from unifideck.core.types import Result
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
            base_path = str(Path.home() / "Games")

        title: str = opts.pop("title", "") or opts.pop("game_title", "")
        # GOG multi-language picker selection (verbatim — it's one of
        # the game's own language codes, matched exactly downstream).
        # Other stores don't send this.
        language = opts.pop("language", None)

        logger.info(
            "[download] install_game store=%s game_id=%s storage=%s "
            "base_path=%s title=%s language=%s",
            store, game_id, storage_type, base_path, title, language,
        )

        download_svc = self._require_download()
        result = await download_svc.add(
            store=store,
            game_id=game_id,
            install_path=base_path,
            title=title,
            is_update=False,
            language=language,
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

    async def uninstall_game(self, app_id: int, delete_prefix: bool = False) -> Any:
        """Uninstall a game via the responsible store connector.

        The frontend only has the Steam ``app_id`` on hand (the
        trash button / Uninstall pill live on the app details page),
        so — like :meth:`update_game` — we resolve it back to its
        ``(store, game_id)`` via the sync layer and dispatch to the
        store. ``delete_prefix`` is forwarded so connectors that run
        games under Proton can also remove the Wine prefix.

        Earlier this method took ``(store, game_id)`` directly, but
        the frontend sends a single ``app_id``; the numeric value
        landed in the ``store`` slot and ``game_id`` was missing, so
        the call raised before anything was uninstalled.
        """
        info = self.sync_service.get_game_info(app_id) if self.sync_service else None
        if not info:
            return Result(success=False, error="game_not_found")

        store, game_id = self._validate_pair(
            info.get("store", ""), info.get("store_game_id", ""),
        )

        logger.info("[download] uninstall_game app_id=%s store=%s game_id=%s delete_prefix=%s",
                     app_id, store, game_id, delete_prefix)

        # Return the ``Result`` dataclass (not a plain ``{success, error}``
        # dict). The RPC envelope folds a dict that already has a top-level
        # ``success`` key into the envelope and leaves ``data=None`` — the
        # frontend then receives ``null`` and ``result?.success`` is always
        # falsy, so ``useGameActions`` never invalidates the game-info cache
        # and the Play section stays "installed" until a manual reload.
        # A dataclass return lands in ``data`` as ``{success, error, ...}``.
        result = await self._require_store(store).uninstall_game(
            game_id, delete_prefix=delete_prefix,
        )
        # Guarantee the install dir is gone even if the store no-op'd. GOG
        # resolves install dirs by scanning its default download_dir, so a
        # game installed elsewhere (SD/custom) can't be found and its
        # uninstall returns success without deleting anything; nile likewise
        # leaves our manifest marker (a stub dir) behind. The marker proves
        # the folder is ours, so this only ever removes a dir we created.
        await asyncio.to_thread(marker_sweep.sweep_game, store, game_id)
        return result

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

    async def get_gog_game_languages(self, game_id: str) -> Any:
        """Return the install languages available for a GOG game.

        Drives the language-select modal in the GOG install flow
        (``useInstallFlow``). Wraps ``GOGStore.get_available_languages``;
        falls back to ``["en-US"]`` so the frontend can still install
        if the lookup fails. ``game_id`` is the GOG product id
        (the game's ``store_game_id``), not the unifideck app id.

        Lost in the mixin refactor — the frontend route existed with
        no handler, so the call errored (swallowed by a ``.catch``)
        and multi-language GOG titles never prompted.
        """
        try:
            store = self.registry.get_store("gog")
            if store is None:
                return {"success": False, "error": "store_not_found", "languages": ["en-US"]}
            languages = await store.get_available_languages(game_id)
            return {"success": True, "languages": languages}
        except Exception as e:
            logger.exception("[download] get_gog_game_languages(%s) failed", game_id)
            return {"success": False, "error": str(e), "languages": ["en-US"]}

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
        path = str(Path.home() / "Games")
        logger.debug("[download] resolved internal → %s", path)
        return path
    if storage_type == "sdcard":
        return _first_external_games_path()
    if storage_type == "custom":
        return _custom_path(config)
    logger.warning("[download] unknown storage type: %s", storage_type)
    return None


def _stat_dev(p: Path) -> int:
    """Return the ``st_dev`` of *p*, or -1 on error (never matches)."""
    try:
        return p.stat().st_dev
    except OSError:
        return -1


def _is_external_mount(mp: str, fstype: str, home_dev: int) -> bool:
    """True if *mp* is a writable mount on a device other than ``$HOME``."""
    if fstype in _SKIP_FSTYPES:
        return False
    dev = _stat_dev(Path(mp))
    if dev == -1 or dev == home_dev:
        return False
    return os.access(mp, os.W_OK)


def _first_external_games_path() -> str | None:
    """The sdcard target: the first external mount's ``Games/`` dir."""
    home_dev = _stat_dev(Path.home())
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue
            mp, fstype = parts[1], parts[2]
            if not _is_external_mount(mp, fstype, home_dev):
                continue
            games_path = Path(mp) / "Games"
            games_path.mkdir(parents=True, exist_ok=True)
            logger.debug(
                "[download] resolved sdcard → %s (%s)", games_path, fstype,
            )
            return str(games_path)
    except OSError as e:
        logger.warning("[download] sdcard resolution failed: %s", e)
    return None


def _custom_path(config: Any) -> str | None:
    """Read ``download.custom_path`` from config; None if unset/invalid."""
    if config is None:
        return None
    try:
        path = config.get("download.custom_path", None)
    except Exception as e:
        logger.warning("[download] custom_path lookup failed: %s", e)
        return None
    if isinstance(path, str) and path:
        logger.debug("[download] resolved custom → %s", path)
        return path
    return None
