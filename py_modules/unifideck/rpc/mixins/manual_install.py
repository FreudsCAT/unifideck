"""ManualInstallRPCMixin — add / finalize manually installed games.

py_modules/unifideck/rpc/mixins/manual_install.py

The Manual Install flow (Settings → MANUAL INSTALL) in three acts:

1. ``manual_install_start(installer_path, title)`` — register the game
   in the manual store's state, create its install directory, write its
   Steam shortcut + games.map row (pointing at the INSTALLER), then
   emit ``MANUAL_INSTALL_LAUNCH_REQUESTED``. The frontend reacts by
   RunGame-ing the shortcut, so the installer wizard opens inside a
   gamescope session (a bare backend subprocess would be invisible in
   Gaming Mode — same constraint as the wrapper stores). The launcher
   creates the Proton prefix on the way and maps drive ``D:`` to the
   install directory, so the user installs onto the real filesystem.
2. ``manual_install_finalize(game_id, exe_path)`` — once the installer
   exits the frontend asks the user for the game's executable and calls
   this: the record flips to ``ready``, the games.map row is re-pointed
   at the real exe, a discovery manifest is written, and artwork /
   metadata enrichment kicks off.
3. ``manual_install_status(game_id)`` — lets the frontend decide, after
   the installer app stops, whether the exe still needs choosing.

A background library sync is requested after each mutation so the game
shows up in the Downloads tab (``get_all_unifideck_games``) and gets
its unifiDB metadata + artwork through the normal enrichment phases.
"""
from __future__ import annotations

import asyncio
import binascii
import logging
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from unifideck.core.manifest import write_manifest
from unifideck.core.types.events import Events
from unifideck.core.types.identifiers import (
    InvalidIdentifierError,
    validate_game_id,
)
from unifideck.rpc.errors import RpcError
from unifideck.stores.manual.shortcut import ensure_manual_game_shortcut
from unifideck.stores.manual.state import (
    STATUS_INSTALLING,
    STATUS_READY,
    ManualGameRecord,
)

logger = logging.getLogger(__name__)

_MAX_TITLE_LENGTH = 120
_INSTALLER_SUFFIXES = (".exe", ".msi")


def _derive_game_id(title: str, installer_path: str) -> str:
    """Stable id: title slug + CRC of the installer path.

    The CRC keeps two different installers with the same title apart,
    and re-adding the SAME installer reuses the existing record instead
    of duplicating it.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:32] or "game"
    digest = f"{binascii.crc32(installer_path.encode('utf-8')) & 0xFFFFFFFF:08x}"
    return f"{slug}-{digest}"


def _validated_installer(installer_path: str) -> Path:
    """The installer as an absolute, existing ``.exe``/``.msi`` file."""
    if not installer_path or not isinstance(installer_path, str):
        raise RpcError("invalid_args", field="installer_path")
    path = Path(installer_path).expanduser()
    if not path.is_absolute():
        raise RpcError("invalid_installer", reason="path_not_absolute")
    if path.suffix.lower() not in _INSTALLER_SUFFIXES:
        raise RpcError("invalid_installer", reason="not_an_installer")
    if not path.is_file():
        raise RpcError("invalid_installer", reason="file_not_found")
    return path


def _is_under(child: Path, parent: Path) -> bool:
    """Strict containment on resolved paths."""
    try:
        base = str(parent.resolve())
        target = str(child.resolve())
        return os.path.commonpath([base, target]) == base
    except (OSError, ValueError):
        return False


def _resolve_finalize_paths(
    record: ManualGameRecord, exe_path: str, prefix_dir: Path,
) -> tuple[str, str, str]:
    """Validate the chosen exe; return ``(exe, install_dir, exe_rel)``.

    Synchronous (run via ``to_thread``). The exe must exist and live
    inside one of the game's legitimate locations:

    * the game's install dir (installed onto drive D:, the normal flow);
    * its Proton prefix (the user installed onto C: after all);
    * the directory the "installer" itself lives in — which is how an
      ALREADY-installed game is added: the user picks the game's own
      exe as the installer, and its folder is the install. Uninstall
      never deletes such a user-managed folder (see
      ``ManualStore._delete_game_dir``).

    Anything else is rejected as a traversal attempt. When the exe is
    outside the game dir, the install dir is re-anchored on the exe's
    own directory so size and save-location resolution stay meaningful.
    """
    if not exe_path or not isinstance(exe_path, str):
        raise RpcError("invalid_args", field="exe_path")
    exe = Path(exe_path).expanduser()
    if not exe.is_file():
        raise RpcError("invalid_executable", reason="file_not_found")
    install_dir = Path(record.install_path).expanduser()
    installer_dir = Path(record.installer_path).expanduser().parent
    confined = (
        _is_under(exe, install_dir)
        or _is_under(exe, prefix_dir)
        or _is_under(exe, installer_dir)
    )
    if not confined:
        raise RpcError("invalid_executable", reason="outside_install_dirs")
    if not _is_under(exe, install_dir):
        install_dir = exe.parent
    rel = os.path.relpath(str(exe), str(install_dir)).replace(os.sep, "/")
    return str(exe), str(install_dir), rel


class ManualInstallRPCMixin:
    """RPC surface for the Manual Install flow."""

    registry: Any
    services: Any
    sync_service: Any
    config: Any
    bus: Any

    # Strong refs to fire-and-forget enrichment syncs (RUF006).
    _manual_sync_tasks: set[asyncio.Task[Any]] = set()

    def _manual_store(self) -> Any:
        """The registered manual store adapter, or ``store_not_found``."""
        adapter = self.registry.get_store("manual") if self.registry else None
        if adapter is None:
            raise RpcError("store_not_found", store="manual")
        return adapter

    def _request_background_sync(self) -> None:
        """Queue a library sync so caches / metadata / artwork catch up."""
        svc = self.sync_service
        if svc is None:
            return
        task = asyncio.create_task(svc.sync_all(source="manual_install"))
        self._manual_sync_tasks.add(task)
        task.add_done_callback(self._manual_sync_tasks.discard)

    async def manual_install_start(
        self, installer_path: str, title: str,
    ) -> Any:
        """Register a manual game and hand its installer to the frontend."""
        record = await self._manual_register_record(installer_path, title)
        app_id = await self._manual_write_shortcut(
            game_id=record.game_id,
            title=record.title,
            exe_path=record.installer_path,
            install_path=record.install_path,
        )
        await self.bus.emit(
            Events.MANUAL_INSTALL_LAUNCH_REQUESTED,
            store_game_id=f"manual:{record.game_id}",
        )
        await self.bus.emit(
            Events.ARTWORK_REQUEST,
            app_id=app_id,
            title=record.title,
            store="manual",
            game_id=record.game_id,
        )
        self._request_background_sync()
        logger.info(
            "[ManualInstall] start %s (installer=%s app_id=%d)",
            record.game_id, record.installer_path, app_id,
        )
        return {
            "success": True,
            "game_id": record.game_id,
            "app_id": app_id,
            "install_path": record.install_path,
        }

    async def _manual_register_record(
        self, installer_path: str, title: str,
    ) -> ManualGameRecord:
        """Validate the inputs and persist the ``installing`` record."""
        store = self._manual_store()
        clean_title = (title or "").strip()[:_MAX_TITLE_LENGTH]
        if not clean_title:
            raise RpcError("invalid_args", field="title")
        installer = await asyncio.to_thread(_validated_installer, installer_path)
        try:
            game_id = validate_game_id(
                _derive_game_id(clean_title, str(installer)),
            )
        except InvalidIdentifierError as e:
            raise RpcError("invalid_identifier", reason=str(e)) from e

        install_dir = store.install_root() / game_id
        await asyncio.to_thread(install_dir.mkdir, parents=True, exist_ok=True)
        record = ManualGameRecord(
            game_id=game_id,
            title=clean_title,
            installer_path=str(installer),
            install_path=str(install_dir),
            status=STATUS_INSTALLING,
        )
        await store.upsert_record(record)
        return record

    async def _manual_write_shortcut(
        self, *, game_id: str, title: str, exe_path: str, install_path: str,
    ) -> int:
        """Shortcut + games.map row for one manual game; returns the appid."""
        shortcut_svc = getattr(self.services, "shortcut", None)
        if shortcut_svc is None:
            raise RpcError("service_unavailable", service="shortcut")
        plugin_dir = os.environ.get("DECKY_PLUGIN_DIR")
        app_id = await ensure_manual_game_shortcut(
            shortcut_svc,
            game_id=game_id,
            title=title,
            install_path=install_path,
            plugin_dir=plugin_dir,
        )
        if app_id is None:
            raise RpcError("shortcut_write_failed", game_id=game_id)
        marked = await shortcut_svc.mark_installed(
            "manual", game_id, exe_path, install_path,
        )
        if marked is None:
            raise RpcError("games_map_write_failed", game_id=game_id)
        return app_id

    async def manual_install_finalize(
        self, game_id: str, exe_path: str,
    ) -> Any:
        """Point a pending manual game at its real executable."""
        store = self._manual_store()
        record = await store.get_record(game_id)
        if record is None:
            raise RpcError("game_not_found", game_id=game_id)

        exe, install_dir, rel = await asyncio.to_thread(
            _resolve_finalize_paths,
            record, exe_path, store.prefix_path(game_id),
        )
        record.exe_path = exe
        record.install_path = install_dir
        record.status = STATUS_READY
        await store.upsert_record(record)

        await write_manifest(
            install_dir, "manual", game_id, record.title, rel,
            platform="windows", config=self.config,
        )
        await self._manual_write_shortcut(
            game_id=game_id,
            title=record.title,
            exe_path=exe,
            install_path=install_dir,
        )
        await self.bus.emit(
            Events.GAME_INSTALLED,
            store="manual",
            game_id=game_id,
            title=record.title,
            install_path=install_dir,
            executable=exe,
        )
        self._request_background_sync()
        logger.info("[ManualInstall] finalized %s → %s", game_id, exe)
        return {"success": True, "game_id": game_id, "executable": exe}

    async def manual_install_status(self, game_id: str) -> Any:
        """Current record for one manual game (or ``exists: False``)."""
        store = self._manual_store()
        record = await store.get_record(game_id)
        if record is None:
            return {"success": True, "exists": False}
        return {"success": True, "exists": True, **asdict(record)}
