"""ManualInstallRPCMixin — add / import / finalize manually installed games.

py_modules/unifideck/rpc/mixins/manual_install.py

The Manual Install flow (Settings → MANUAL INSTALL) exposes five RPCs:

1. ``manual_install_start(installer_path, title)`` — register the game
   in the manual store's state, create its install directory, write its
   Steam shortcut + games.map row (pointing at the INSTALLER), then
   emit ``MANUAL_INSTALL_LAUNCH_REQUESTED``. The frontend reacts by
   RunGame-ing the shortcut, so the installer wizard opens inside a
   gamescope session (a bare backend subprocess would be invisible in
   Gaming Mode — same constraint as the wrapper stores). The launcher
   creates the Proton prefix on the way and maps drive ``D:`` to the
   install directory, so the user installs onto the real filesystem.
2. ``manual_import(exe_path, title)`` — the IMPORT button: add a game
   that is ALREADY installed by picking its executable directly. The
   record is born ``ready``; no installer run, no follow-up modal.
3. ``manual_install_finalize(game_id, exe_path)`` — once the installer
   exits the frontend asks the user for the game's executable and calls
   this: the record flips to ``ready``, the games.map row is re-pointed
   at the real exe, a discovery manifest is written, and artwork /
   metadata enrichment kicks off.
4. ``manual_exe_candidates(game_id)`` — scans the game's install dir
   AND its prefix's ``drive_c`` for candidate ``.exe`` files, so the
   post-install picker offers a list instead of a blind file browser —
   whether the user installed onto D: or onto C:.
5. ``manual_install_status(game_id)`` — lets the frontend decide, after
   the installer app stops, whether the exe still needs choosing.

A background library sync is requested when each flow ENDS (finalize,
and the post-run ``manual_ensure_shortcut``) so the game shows up in
the Downloads tab and gets its unifiDB metadata through the normal
enrichment phases. Deliberately NOT at start/import time: a sync that
runs while the temp-shortcut dance has Steam's flush erasing our vdf
row makes reconcile re-add it, and the sync UI then pops its own
"restart Steam?" prompt in the middle of the flow. Run only when the
row is freshly in place, reconcile sees no change and stays quiet —
the manual flow owns its single, correctly-timed restart prompt.
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
_MAX_CANDIDATES = 40
# drive_c top-level directories that are Wine plumbing, never a game.
_PREFIX_SYSTEM_DIRS = frozenset({"windows", "users", "programdata"})
# Wine/Proton stock directories that live INSIDE ``Program Files*`` —
# their exes (iexplore, wmplayer, …) are what used to flood the
# candidate list on C: scans.
_WINE_STOCK_DIRS = frozenset({
    "internet explorer",
    "windows nt",
    "windows media player",
    "windows photo viewer",
    "common files",
    "modern ui",
})
# Stock Wine executables that are never a game, wherever they appear.
_WINE_STOCK_EXES = frozenset({
    "iexplore.exe", "wmplayer.exe", "explorer.exe", "notepad.exe",
    "regedit.exe", "wordpad.exe", "winemenubuilder.exe", "winhlp32.exe",
    "hh.exe", "winver.exe", "rundll32.exe", "control.exe", "conhost.exe",
    "start.exe", "wscript.exe", "cscript.exe", "wineboot.exe",
    "msiexec.exe", "taskmgr.exe", "cmd.exe", "winecfg.exe",
})


def _derive_game_id(title: str, installer_path: str) -> str:
    """Stable id: title slug + CRC of the installer path.

    The CRC keeps two different installers with the same title apart,
    and re-adding the SAME installer reuses the existing record instead
    of duplicating it.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:32] or "game"
    digest = f"{binascii.crc32(installer_path.encode('utf-8')) & 0xFFFFFFFF:08x}"
    return f"{slug}-{digest}"


def _validated_identity(title: str, source_path: str) -> tuple[str, str]:
    """Clean the title and derive the record's game id from it."""
    clean_title = (title or "").strip()[:_MAX_TITLE_LENGTH]
    if not clean_title:
        raise RpcError("invalid_args", field="title")
    try:
        game_id = validate_game_id(_derive_game_id(clean_title, source_path))
    except InvalidIdentifierError as e:
        raise RpcError("invalid_identifier", reason=str(e)) from e
    return clean_title, game_id


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


def _extra_allowed_roots() -> tuple[Path, ...]:
    """Where a game may legitimately live besides its own dirs.

    Anywhere under the user's home (Wine's ``Z:`` maps the whole
    filesystem, so an installer can land in any folder the user picks)
    and under the removable-storage mount root. Widening this is safe
    against data loss because uninstall only ever deletes directories
    inside the manual install root (``ManualStore._delete_game_dir``).
    """
    return (Path.home(), Path("/run/media"))


def _validated_game_exe(exe_path: str, allowed_roots: tuple[Path, ...]) -> Path:
    """The chosen exe as an existing ``.exe`` inside an allowed root."""
    if not exe_path or not isinstance(exe_path, str):
        raise RpcError("invalid_args", field="exe_path")
    exe = Path(exe_path).expanduser()
    if not exe.is_file():
        raise RpcError("invalid_executable", reason="file_not_found")
    if not any(_is_under(exe, root) for root in allowed_roots):
        raise RpcError("invalid_executable", reason="outside_install_dirs")
    return exe


def _resolve_finalize_paths(
    record: ManualGameRecord,
    exe_path: str,
    prefix_dir: Path,
    extra_roots: tuple[Path, ...] | None = None,
) -> tuple[str, str, str]:
    """Validate the chosen exe; return ``(exe, install_dir, exe_rel)``.

    Synchronous (run via ``to_thread``). The exe may live in the game's
    install dir (drive D:, the normal flow), its Proton prefix (the
    user installed onto C:), the "installer"'s own directory, or — via
    ``_extra_allowed_roots`` — anywhere under the user's home or the
    removable mounts, because Wine's ``Z:`` drive lets an installer
    target any folder the user picks. Anything else (system paths) is
    rejected. When the exe is outside the game dir, the install dir is
    re-anchored on the exe's own directory so size and save-location
    resolution stay meaningful; uninstall only ever deletes dirs under
    the manual install root, so a re-anchored dir is never at risk.
    """
    install_dir = Path(record.install_path).expanduser()
    installer_dir = Path(record.installer_path).expanduser().parent
    if extra_roots is None:
        extra_roots = _extra_allowed_roots()
    exe = _validated_game_exe(
        exe_path, (install_dir, prefix_dir, installer_dir, *extra_roots),
    )
    if not _is_under(exe, install_dir):
        install_dir = exe.parent
    rel = os.path.relpath(str(exe), str(install_dir)).replace(os.sep, "/")
    return str(exe), str(install_dir), rel


def _resolve_import_paths(
    exe_path: str, extra_roots: tuple[Path, ...] | None = None,
) -> tuple[str, str, str]:
    """Validate an IMPORTed game's exe; return ``(exe, install_dir, rel)``.

    Synchronous (run via ``to_thread``). The exe's own directory is the
    install dir — the files stay exactly where they are.
    """
    if extra_roots is None:
        extra_roots = _extra_allowed_roots()
    exe = _validated_game_exe(exe_path, extra_roots)
    if exe.suffix.lower() != ".exe":
        raise RpcError("invalid_executable", reason="not_an_exe")
    return str(exe), str(exe.parent), exe.name


def _candidates_from(
    root: Path, rel_prefix: str, *, in_prefix: bool, installer: str,
) -> list[dict[str, Any]]:
    """Filtered candidate entries under one scan root.

    Reuses the executable mixin's noise-filtered scan, then drops Wine
    stock executables, anything living in a Wine stock directory (only
    meaningful inside a prefix) and the game's own installer.
    """
    from .executable import _scan_executables

    out: list[dict[str, Any]] = []
    for rel in _scan_executables(str(root)):
        name = os.path.basename(rel)
        if name.lower() in _WINE_STOCK_EXES:
            continue
        segments = {s.lower() for s in rel.split("/")[:-1]}
        if in_prefix and segments & _WINE_STOCK_DIRS:
            continue
        abs_path = str(root / rel)
        if abs_path == installer:
            continue
        out.append({
            "path": abs_path,
            "rel": f"{rel_prefix}{rel}",
            "name": name,
            "in_prefix": in_prefix,
        })
    return out


def _scan_candidate_exes(
    record: ManualGameRecord, prefix_dir: Path,
) -> list[dict[str, Any]]:
    """Candidate game exes from the install dir and the prefix's C: drive.

    Synchronous (run via ``to_thread``). The prefix scan walks each
    non-system top-level directory of ``drive_c`` (``Program Files*``,
    ``Games``, …) so Wine's own plumbing never floods the list.
    """
    from unifideck.launcher.proton.infrastructure.prefix_layout import (
        resolve_drive_c,
    )

    installer = str(Path(record.installer_path).expanduser())
    out: list[dict[str, Any]] = []
    install_dir = Path(record.install_path).expanduser()
    if install_dir.is_dir():
        out.extend(
            _candidates_from(
                install_dir, "", in_prefix=False, installer=installer,
            ),
        )
    drive_c = resolve_drive_c(prefix_dir)
    if drive_c is not None:
        for child in sorted(drive_c.iterdir()):
            if not child.is_dir() or child.name.lower() in _PREFIX_SYSTEM_DIRS:
                continue
            out.extend(
                _candidates_from(
                    child,
                    f"{child.name}/",
                    in_prefix=True,
                    installer=installer,
                ),
            )
    return out[:_MAX_CANDIDATES]


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

    async def manual_import(self, exe_path: str, title: str) -> Any:
        """IMPORT: add an already-installed game from its executable.

        The record is born ``ready`` — no installer run, no follow-up
        exe modal. Files stay where they are (and uninstall will never
        delete them: the install dir is outside the manual root).
        """
        store = self._manual_store()
        exe, install_dir, rel = await asyncio.to_thread(
            _resolve_import_paths, exe_path,
        )
        clean_title, game_id = _validated_identity(title, exe)
        record = ManualGameRecord(
            game_id=game_id,
            title=clean_title,
            installer_path=exe,
            install_path=install_dir,
            exe_path=exe,
            status=STATUS_READY,
        )
        app_id = await self._manual_activate(store, record, rel)
        # Verification run: the frontend RunGames the freshly imported
        # game so the prefix gets created NOW and the user sees it
        # actually launches — later runs are then instant.
        await self.bus.emit(
            Events.MANUAL_INSTALL_LAUNCH_REQUESTED,
            store_game_id=f"manual:{game_id}",
        )
        await self.bus.emit(
            Events.ARTWORK_REQUEST,
            app_id=app_id,
            title=clean_title,
            store="manual",
            game_id=game_id,
        )
        logger.info("[ManualInstall] imported %s → %s", game_id, exe)
        return {"success": True, "game_id": game_id, "app_id": app_id}

    async def _manual_register_record(
        self, installer_path: str, title: str,
    ) -> ManualGameRecord:
        """Validate the inputs and persist the ``installing`` record."""
        store = self._manual_store()
        installer = await asyncio.to_thread(_validated_installer, installer_path)
        clean_title, game_id = _validated_identity(title, str(installer))

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

    async def _manual_activate(
        self, store: Any, record: ManualGameRecord, exe_rel: str,
    ) -> int:
        """Persist a READY record + manifest + shortcut; emit GAME_INSTALLED.

        The discovery manifest is an OWNERSHIP marker — cleanup's marker
        sweep deletes any directory carrying one. So it is only written
        into directories the plugin itself created (under the manual
        install root), NEVER into a user-managed folder (an imported
        game, or an install re-anchored outside the root): planting it
        there once cost a user their game files.
        """
        await store.upsert_record(record)
        managed = await asyncio.to_thread(
            _is_under,
            Path(record.install_path), store.install_root(),
        )
        if managed:
            await write_manifest(
                record.install_path, "manual", record.game_id, record.title,
                exe_rel, platform="windows", config=self.config,
            )
        app_id = await self._manual_write_shortcut(
            game_id=record.game_id,
            title=record.title,
            exe_path=record.exe_path,
            install_path=record.install_path,
        )
        await self.bus.emit(
            Events.GAME_INSTALLED,
            store="manual",
            game_id=record.game_id,
            title=record.title,
            install_path=record.install_path,
            executable=record.exe_path,
        )
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
        await self._manual_activate(store, record, rel)
        self._request_background_sync()
        logger.info("[ManualInstall] finalized %s → %s", game_id, exe)
        return {"success": True, "game_id": game_id, "executable": exe}

    async def manual_exe_candidates(self, game_id: str) -> Any:
        """Candidate exes for the post-install picker (D: and C: scans)."""
        store = self._manual_store()
        record = await store.get_record(game_id)
        if record is None:
            raise RpcError("game_not_found", game_id=game_id)
        candidates = await asyncio.to_thread(
            _scan_candidate_exes, record, store.prefix_path(game_id),
        )
        return {
            "success": True,
            "install_path": record.install_path,
            "candidates": candidates,
        }

    async def manual_ensure_shortcut(self, game_id: str) -> Any:
        """Re-write the game's persistent shortcut + games.map row.

        Steam flushes ITS in-memory copy of ``shortcuts.vdf`` whenever
        the temp-shortcut dance calls ``AddShortcut``/``RemoveShortcut``
        — and that copy never contained our persistent row, so every
        manual launch erased it (the "tile never appears after restart"
        bug). The frontend calls this once the launched app has stopped
        and the temp shortcut is cleaned up, so the row lands AFTER the
        last flush and survives the next restart.
        """
        store = self._manual_store()
        record = await store.get_record(game_id)
        if record is None:
            raise RpcError("game_not_found", game_id=game_id)
        app_id = await self._manual_write_shortcut(
            game_id=game_id,
            title=record.title,
            exe_path=record.exe_path or record.installer_path,
            install_path=record.install_path,
        )
        # The row is freshly in place — a sync now reconciles to "no
        # change" (no spurious restart prompt) while still bringing the
        # game into the Downloads tab and the metadata phases.
        self._request_background_sync()
        return {"success": True, "game_id": game_id, "app_id": app_id}

    async def manual_install_status(self, game_id: str) -> Any:
        """Current record for one manual game (or ``exists: False``)."""
        store = self._manual_store()
        record = await store.get_record(game_id)
        if record is None:
            return {"success": True, "exists": False}
        return {"success": True, "exists": True, **asdict(record)}
