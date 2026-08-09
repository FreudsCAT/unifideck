"""Battle.net store — the ``StoreBase`` implementation.

py_modules/unifideck/stores/battlenet/store.py

A launcher-wrapper store: the real Battle.net Windows client runs inside a
Proton prefix and does the downloading and launching. There is no CLI and
there will not be one — Heroic requires a CLI to exist before adding a
store, and none of the NGDP projects ship a downloader.

Ownership is read from the **client's own local state**, not from the web:
``CachedData.db`` holds the account's licence ids, and the cached PUB
catalog turns them into playable titles by evaluating a small rule
language. The web endpoint (``games-and-subs``) supplies the
``game_account`` facts those rules need for free-to-play and subscription
titles, and is optional enrichment rather than the source of truth.

Consequence: the library is empty until the user has signed into the client
once. That is not a new constraint — install and launch already require it.

Delegation only. Every concern lives in its own module (``ownership/``,
``prefix/``, ``library``, ``id_map``), mirroring the Ubisoft layout.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types.domain import Game, StoreInfo
from unifideck.core.types.results import AuthResult, InstallResult, Result
from unifideck.stores.shared.store_base import StoreBase

from . import config as store_config
from . import library as library_mod
from . import paths
from .id_map import BattlenetIdMap
from .ownership import read_catalog
from .prefix import BattlenetPrefixManager, inspect_prefix

if TYPE_CHECKING:
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus import EventBus

logger = logging.getLogger(__name__)


class BattlenetStore(StoreBase):
    """Blizzard Battle.net, driven through the vendor client in a prefix."""

    store_info = StoreInfo(
        name="battlenet",
        display_name="Battle.net",
        auth_method="shortcut",
        icon_asset="battlenet.png",
        uses_wine=True,
        supports_install=True,
        supports_cloud_saves=False,
    )

    def __init__(
        self,
        bus: EventBus,
        cache: CacheManager,
        plugin_dir: str | None = None,
        config: Any | None = None,
    ) -> None:
        super().__init__(bus, cache, plugin_dir, config)
        self.config = store_config.from_config_manager(config)
        self.prefixes = BattlenetPrefixManager(self.config.prefixes_dir_path)
        self.id_map = BattlenetIdMap(self.config.id_map_path)
        # Injected post-discovery by services/bootstrap/store_injector.py.
        self._shortcut_service: Any | None = None
        self._edge: Any | None = None

    # -- helpers -----------------------------------------------------------

    @property
    def _auth_drive_c(self) -> Path | None:
        """drive_c of the prefix the user signed into, if it exists."""
        return paths.drive_c(self.prefixes.auth_prefix)

    def _launcher_path(self) -> str:
        base = Path(self._plugin_dir) if self._plugin_dir else Path()
        return str(base / "bin" / "unifideck-launcher")

    def _game_account_programs(self) -> frozenset[str]:
        """Programs the account has a game account for.

        Supplied by the optional web enrichment; empty is safe and simply
        means free-to-play titles the user has touched will not appear
        until that runs.
        """
        cached = self._cached_game_accounts()
        return frozenset(cached)

    def _cached_game_accounts(self) -> set[str]:
        try:
            raw = self._cache.get("battlenet", "game_accounts")
        except Exception:  # cache miss must never break a library read
            return set()
        return set(raw) if isinstance(raw, (list, set, tuple)) else set()

    # -- StoreBase ---------------------------------------------------------

    async def is_available(self) -> bool:
        """Signed in when the client prefix holds a usable licence ledger.

        Keyed on the *auth* prefix rather than on any game prefix, so a
        user who has connected but not installed anything still shows as
        connected.
        """
        drive_c = self._auth_drive_c
        if drive_c is None:
            return False
        from .ownership import read_licences

        return read_licences(drive_c).is_usable

    async def start_auth(self, **_kwargs: Any) -> AuthResult:
        """Open the vendor client so the user can sign in.

        The client login is the primary credential: it produces both the
        licence ledger and the cached catalog. The frontend drives this by
        RunGame-ing an auth shortcut, because a backend-spawned process has
        no gamescope session in Gaming Mode.
        """
        status = inspect_prefix(self.prefixes.auth_prefix)
        if not status.usable:
            return AuthResult(
                success=False,
                error="Battle.net client is not installed yet",
                error_code="client_not_installed",
                store=self.store_name,
                metadata={"needs_bootstrap": True},
            )
        return AuthResult(
            success=True,
            store=self.store_name,
            next_step="client_login",
            metadata={"pending": True, "prefix": str(self.prefixes.auth_prefix)},
        )

    async def complete_auth(self, **_kwargs: Any) -> AuthResult:
        signed_in = await self.is_available()
        return AuthResult(
            success=signed_in,
            store=self.store_name,
            error=None if signed_in else "Battle.net client is not signed in",
            error_code=None if signed_in else "not_signed_in",
        )

    async def logout(self) -> Result:
        """Forget cached account state. Never touches a prefix.

        Deliberately non-destructive, and the opposite of Ubisoft's logout:
        for this store the prefix *is* the install, so wiping prefixes here
        would delete the user's games. Signing the client out is a separate,
        explicitly-labelled action.
        """
        try:
            self._cache.clear("battlenet")
        except Exception:
            logger.warning("[Battlenet] cache invalidate failed during logout")
        await self._emit_logout()
        return Result(success=True, store=self.store_name)

    async def _emit_logout(self) -> None:
        from unifideck.core.types.events import Events

        await self._emit(Events.STORE_LOGOUT, store=self.store_name)

    async def get_library(self, *, force: bool = False) -> list[Game] | None:
        """Owned + installed titles, read entirely from client-local state."""
        drive_c = self._auth_drive_c
        if drive_c is None:
            logger.info("[Battlenet] no client prefix yet — empty library")
            return []

        import asyncio

        catalog = await asyncio.to_thread(read_catalog, drive_c)
        if not catalog.program_configurations:
            logger.warning(
                "[Battlenet] PUB catalog cache is empty — launch the client "
                "once so it populates",
            )
        facts = await asyncio.to_thread(
            library_mod.read_account_facts, drive_c, self._game_account_programs(),
        )
        installed = await asyncio.to_thread(self._collect_installed)
        games = library_mod.build_library(
            catalog, facts, installed, launcher_path=self._launcher_path(),
        )
        logger.info(
            "[Battlenet] library: %d titles (%d installed, force=%s)",
            len(games),
            sum(1 for g in games if g.installed),
            force,
        )
        return games

    def _collect_installed(self) -> dict[str, Any]:
        """Install state across every prefix we have recorded."""
        merged: dict[str, Any] = {}
        for prefix in self.id_map.all_prefix_paths():
            drive_c = paths.drive_c(prefix)
            if drive_c is None:
                continue
            merged.update(library_mod.read_install_state(drive_c, prefix))
        return merged

    async def install_game(self, game_id: str, **kwargs: Any) -> InstallResult:
        """Hand the install to the client; completion is polled elsewhere.

        ``--exec="install <FAMILY>"`` does **not** start a download — that
        was measured against the current client with a known-good family
        code. The install is a user click inside the client, exactly as it
        is for Ubisoft, so this prepares the prefix and signals the frontend
        to bring the client up.
        """
        del kwargs
        prefix = await self.prefixes.create_game_prefix(game_id)
        if prefix is None:
            return InstallResult(
                success=False,
                game_id=game_id,
                store=self.store_name,
                error="Battle.net client template is not ready",
                error_code="template_not_ready",
            )
        self.id_map.merge(game_id, prefix_path=str(prefix))
        return InstallResult(
            success=True,
            game_id=game_id,
            store=self.store_name,
            install_path=str(prefix),
            metadata={"phase": "manual", "prefix": str(prefix)},
        )

    async def uninstall_game(self, game_id: str, **kwargs: Any) -> Result:
        """Remove the game by removing its prefix — the install lives inside."""
        delete_prefix = bool(kwargs.get("delete_prefix", True))
        prefix = self.id_map.resolve_prefix(game_id)
        if prefix is None:
            return Result(
                success=False,
                store=self.store_name,
                error="No recorded prefix for this game",
                error_code="prefix_unknown",
            )
        if delete_prefix and not self.prefixes.remove_game_prefix(prefix):
            return Result(
                success=False,
                store=self.store_name,
                error="Refused to remove a prefix Unifideck did not create",
                error_code="prefix_not_owned",
            )
        self.id_map.forget(game_id)
        from unifideck.core.types.events import Events

        await self._emit(Events.GAME_UNINSTALLED, store=self.store_name, game_id=game_id)
        return Result(success=True, store=self.store_name)

    async def update_game(self, game_id: str, **kwargs: Any) -> InstallResult:
        """Updates are client-driven, same shape as install."""
        del kwargs
        prefix = self.id_map.resolve_prefix(game_id)
        if prefix is None:
            return InstallResult(
                success=False,
                game_id=game_id,
                store=self.store_name,
                error="No recorded prefix for this game",
                error_code="prefix_unknown",
            )
        return InstallResult(
            success=True,
            game_id=game_id,
            store=self.store_name,
            install_path=str(prefix),
            metadata={"phase": "manual", "prefix": str(prefix)},
        )

    async def check_for_updates(self) -> list[str]:
        """Not implemented: the client applies updates on its own.

        ``product.db`` exposes the installed version, but there is no
        authenticated per-account source for the *available* version that
        does not go through the client. Reporting a guess would produce
        phantom update badges.
        """
        return []

    async def get_game_size(self, game_id: str) -> int | None:
        """Total install size, when the client has finished writing it.

        Comes from ``product.db``, which populates the total only at
        completion — during a download it is 0, meaning "not known yet"
        rather than "empty".
        """
        record = self.id_map.get(game_id)
        if record and record.total_bytes:
            return record.total_bytes
        prefix = self.id_map.resolve_prefix(game_id)
        if prefix is None:
            return None
        drive_c = paths.drive_c(prefix)
        if drive_c is None:
            return None
        import asyncio

        state = await asyncio.to_thread(library_mod.read_install_state, drive_c, prefix)
        for game in state.values():
            if game.total_bytes:
                return game.total_bytes
        return None

    async def get_installed_path(self, game_id: str) -> str | None:
        """Host-side install directory, translated out of Wine syntax."""
        record = self.id_map.get(game_id)
        if record and record.install_path:
            return record.install_path
        prefix = self.id_map.resolve_prefix(game_id)
        if prefix is None:
            return None
        drive_c = paths.drive_c(prefix)
        if drive_c is None:
            return None
        import asyncio

        state = await asyncio.to_thread(library_mod.read_install_state, drive_c, prefix)
        for game in state.values():
            if game.host_install_path:
                return game.host_install_path
        return None
