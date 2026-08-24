"""ManualStore — games the user installs from their own local installers.

py_modules/unifideck/stores/manual/manual_store.py

No vendor account, no CLI, no downloads: the "library" is the JSON
state written by the Manual Install flow (see ``state.py`` and
``rpc/mixins/manual_install.py``). The store's job is to feed that
state into the normal Unifideck machinery:

* ``get_library`` returns every record as an installed ``Game`` (with
  ``exe_path`` populated), so reconcile keeps the shortcut and the
  games.map row alive across syncs and the launcher's generic
  Proton/umu path (``generic_launch`` → ``_raw_exe_launch``) runs it.
* ``uninstall_game`` deletes the game directory (and optionally the
  prefix) and drops the record — the next reconcile sweeps the
  shortcut, and the artwork handler removes the grid files.

Auth methods are honest no-ops (``auth_method="none"`` in
``store_info`` is what hides the Connect button in the frontend).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types import (
    AuthResult,
    Events,
    Game,
    InstallResult,
    Result,
    StoreInfo,
)
from unifideck.stores.shared.installed_size import dir_size_bytes
from unifideck.stores.shared.store_base import StoreBase

from .state import ManualGameRecord, load_records, save_records

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus import EventBus

logger = logging.getLogger(__name__)

DEFAULT_INSTALL_ROOT = "~/Games/Manual"
DEFAULT_STATE_FILE = "~/.local/share/unifideck/manual_games.json"
_PREFIXES_ROOT = "~/.local/share/unifideck/prefixes"


def _is_safe_to_delete(path: Path) -> bool:
    """Refuse to rmtree anything shallow or outside a sane game dir.

    Same spirit as the Epic uninstaller's guard: never the filesystem
    root, never ``$HOME`` itself, and always at least three path
    components deep (``/home/deck/Games/...``).
    """
    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    if resolved in (home, Path("/")):
        return False
    return len(resolved.parts) >= 3


def _guarded_rmtree(raw_path: str, label: str) -> None:
    """Synchronous, guarded recursive delete (run via ``to_thread``)."""
    target = Path(raw_path).expanduser()
    if not target.exists():
        return
    if not _is_safe_to_delete(target):
        logger.error(
            "[ManualStore] refusing to delete unsafe %s %s", label, target,
        )
        return
    shutil.rmtree(target, ignore_errors=True)
    logger.info("[ManualStore] deleted %s %s", label, target)


def _dir_size_or_none(raw_path: str) -> int | None:
    """Synchronous size helper (run via ``to_thread``)."""
    path = Path(raw_path).expanduser()
    if not path.is_dir():
        return None
    return dir_size_bytes(str(path))


class ManualStore(StoreBase):
    """Store adapter for manually installed local games."""

    store_info = StoreInfo(
        name="manual",
        display_name="Manual",
        auth_method="none",
        icon_asset="",
        uses_wine=True,
        supports_install=False,
    )

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        plugin_dir: str | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        """Initialize the instance."""
        super().__init__(bus, cache, plugin_dir, config)
        self._state_lock = asyncio.Lock()

    # ── paths ─────────────────────────────────────────────────────

    def state_path(self) -> Path:
        """Location of the manual-games JSON state file."""
        raw = None
        if self._config is not None:
            raw = self._config.get("stores.manual.state_file")
        return Path(str(raw) if raw else DEFAULT_STATE_FILE).expanduser()

    def install_root(self) -> Path:
        """Root directory manual games are installed under."""
        raw = None
        if self._config is not None:
            raw = self._config.get("stores.manual.install_dir")
        return Path(str(raw) if raw else DEFAULT_INSTALL_ROOT).expanduser()

    def prefix_path(self, game_id: str) -> Path:
        """The launcher's default prefix location for ``game_id``."""
        return Path(_PREFIXES_ROOT).expanduser() / game_id

    # ── state access (used by the Manual Install RPC mixin) ───────

    async def load_state(self) -> dict[str, ManualGameRecord]:
        """Read all records from disk."""
        return await asyncio.to_thread(load_records, self.state_path())

    async def get_record(self, game_id: str) -> ManualGameRecord | None:
        """One record by id, or ``None``."""
        return (await self.load_state()).get(game_id)

    async def upsert_record(self, record: ManualGameRecord) -> None:
        """Insert or replace one record, atomically."""
        async with self._state_lock:
            def _write() -> None:
                records = load_records(self.state_path())
                records[record.game_id] = record
                save_records(self.state_path(), records)
            await asyncio.to_thread(_write)

    async def remove_record(self, game_id: str) -> bool:
        """Drop one record; True when it existed."""
        async with self._state_lock:
            def _write() -> bool:
                records = load_records(self.state_path())
                existed = records.pop(game_id, None) is not None
                if existed:
                    save_records(self.state_path(), records)
                return existed
            return await asyncio.to_thread(_write)

    # ── StoreBase contract ────────────────────────────────────────

    async def is_available(self) -> bool:
        """Always available — there is nothing to install or log into."""
        self._cached_available = True
        return True

    async def start_auth(self, **kwargs: Any) -> AuthResult:
        """No authentication — trivially successful."""
        return AuthResult(success=True, store=self.store_name)

    async def complete_auth(self, **kwargs: Any) -> AuthResult:
        """No authentication — trivially successful."""
        return AuthResult(success=True, store=self.store_name)

    async def logout(self) -> Result:
        """No-op: manual games are local files, not a session to clear."""
        return Result(success=True)

    async def get_library(self, *, force: bool = False) -> list[Game] | None:
        """Every manual record as an installed ``Game``.

        A record still ``installing`` launches its installer (that IS
        the pending action for it), so ``exe_path`` falls back to the
        installer path — which also keeps the games.map row alive for
        the RunGame-driven install flow.
        """
        del force  # no remote cache to bypass
        records = await self.load_state()
        games: list[Game] = []
        for record in records.values():
            exe = record.exe_path or record.installer_path
            games.append(
                Game(
                    app_id=0,
                    store=self.store_name,
                    store_game_id=record.game_id,
                    title=record.title,
                    installed=True,
                    install_path=record.install_path or None,
                    exe_path=exe or None,
                    metadata={"manual_status": record.status},
                ),
            )
        return games

    async def install_game(
        self,
        game_id: str,
        install_path: str | None = None,
        progress_cb: Any = None,
        **kwargs: Any,
    ) -> InstallResult:
        """Installs happen through the Manual Install flow, not the queue.

        The signature still accepts the DownloadWorker's calling
        convention (positional ``install_path`` + ``progress_cb``) so a
        stray Install press fails with a clean result instead of a
        ``TypeError`` inside the worker.
        """
        del install_path, progress_cb
        return InstallResult(
            success=False,
            store=self.store_name,
            game_id=game_id,
            error="manual_store_external_install",
        )

    async def uninstall_game(
        self, game_id: str, **kwargs: Any,
    ) -> Result:
        """Delete the game directory (and optionally its prefix) + record.

        When the game was installed onto C: — its files live INSIDE the
        prefix — the prefix is deleted regardless of the toggle:
        keeping it would silently leak the whole "uninstalled" game on
        disk, and game files and prefix are not separable there.
        """
        record = await self.get_record(game_id)
        if record is None:
            return Result(success=False, error="game_not_found")
        inside_prefix = await asyncio.to_thread(
            self._install_lives_in_prefix, record,
        )
        await self._delete_game_dir(record)
        if kwargs.get("delete_prefix") or inside_prefix:
            if inside_prefix and not kwargs.get("delete_prefix"):
                logger.info(
                    "[ManualStore] %s installed inside its prefix — "
                    "deleting the prefix to avoid leaking the game",
                    game_id,
                )
            await self._delete_prefix(game_id)
        await self.remove_record(game_id)
        await self._emit(
            Events.GAME_UNINSTALLED, store=self.store_name, game_id=game_id,
        )
        logger.info("[ManualStore] uninstalled %s", game_id)
        return Result(success=True)

    def _install_lives_in_prefix(self, record: ManualGameRecord) -> bool:
        """Synchronous: is the record's install dir inside its prefix?"""
        if not record.install_path:
            return False
        try:
            target = Path(record.install_path).expanduser().resolve()
            return target.is_relative_to(
                self.prefix_path(record.game_id).resolve(),
            )
        except OSError:
            return False

    async def _delete_game_dir(self, record: ManualGameRecord) -> None:
        """Best-effort, guarded removal of the record's install dir.

        Only directories under the manual install root are ever
        deleted. A game added from an already-installed folder the
        user manages themselves (``install_path`` re-anchored on the
        chosen exe's directory, e.g. ``~/Jocs/<game>``) is theirs:
        uninstalling forgets the game but never touches those files.
        """
        if not record.install_path:
            return

        def _delete_if_managed() -> None:
            target = Path(record.install_path).expanduser()
            root = self.install_root()
            try:
                managed = target.resolve().is_relative_to(root.resolve())
            except OSError:
                managed = False
            if not managed:
                logger.info(
                    "[ManualStore] leaving user-managed dir %s in place",
                    target,
                )
                # Defuse any ownership marker an earlier build planted
                # here: cleanup's marker sweep treats it as proof the
                # plugin created the directory and would DELETE the
                # user's folder (it happened once).
                from unifideck.core.manifest import DEFAULT_MANIFEST_FILENAME
                marker = target / DEFAULT_MANIFEST_FILENAME
                try:
                    if marker.is_file():
                        marker.unlink()
                        logger.info(
                            "[ManualStore] removed stray marker %s", marker,
                        )
                except OSError as e:
                    logger.warning(
                        "[ManualStore] could not remove marker %s: %s",
                        marker, e,
                    )
                return
            _guarded_rmtree(str(target), "install dir")

        await asyncio.to_thread(_delete_if_managed)

    async def _delete_prefix(self, game_id: str) -> None:
        """Remove the game's Proton prefix (``delete_prefix`` path)."""
        await asyncio.to_thread(
            _guarded_rmtree, str(self.prefix_path(game_id)), "prefix",
        )

    async def update_game(
        self, game_id: str, **kwargs: Any,
    ) -> InstallResult:
        """Nothing to update — the user manages the files."""
        return InstallResult(
            success=True, store=self.store_name, game_id=game_id,
        )

    async def check_for_updates(self) -> list[str]:
        """No update source."""
        return []

    async def get_game_size(self, game_id: str) -> int | None:
        """On-disk size of the game's install directory."""
        record = await self.get_record(game_id)
        if record is None or not record.install_path:
            return None
        return await asyncio.to_thread(_dir_size_or_none, record.install_path)

    async def get_installed_path(self, game_id: str) -> str | None:
        """The record's install directory."""
        record = await self.get_record(game_id)
        return record.install_path or None if record else None
