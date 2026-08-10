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

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types.domain import Game, StoreInfo
from unifideck.core.types.results import AuthResult, InstallResult, Result
from unifideck.event_bus.event_bus_devex import auto_wire
from unifideck.stores.shared.auth_shortcut import (
    AuthShortcutSpec,
    build_context,
)
from unifideck.stores.shared.store_base import StoreBase
from unifideck.stores.shared.wrapper_session_hooks import WrapperSessionHooks

from . import config as store_config
from . import library as library_mod
from . import paths
from .id_map import BattlenetIdMap
from .install import BattlenetInstaller
from .ownership import read_catalog
from .prefix import BattlenetPrefixManager, inspect_prefix

if TYPE_CHECKING:
    from unifideck.core.cache_manager import CacheManager
    from unifideck.event_bus import EventBus

logger = logging.getLogger(__name__)


class BattlenetStore(WrapperSessionHooks, StoreBase):
    """Blizzard Battle.net, driven through the vendor client in a prefix."""

    session_store_id = "battlenet"

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
        self._installer = BattlenetInstaller(
            self.prefixes, self.id_map, self.capture_before_prefix_loss,
        )
        # Injected post-discovery by services/bootstrap/store_injector.py.
        self._shortcut_service: Any | None = None
        # Tell the out-of-process launcher where the shared prefixes are: it
        # runs under the system Python, cannot read our config, and needs the
        # auth prefix to inject the live session before it starts a client.
        self.publish_session_prefixes(self.prefixes.template_prefix)
        # Subscribes ``GAME_STOPPED`` so the token the client rotates during a
        # play session is captured back to the auth prefix.
        auto_wire(self, bus)

    # -- WrapperSessionHooks ----------------------------------------------

    def session_auth_prefix(self) -> Path:
        return self.prefixes.auth_prefix

    def session_prefixes(self) -> list[Path]:
        return list(self.id_map.all_prefix_paths())

    def session_prefix_for(self, game_id: str) -> Path | None:
        return self.id_map.resolve_prefix(game_id)

    # -- helpers -----------------------------------------------------------

    @property
    def _auth_drive_c(self) -> Path | None:
        """drive_c of the prefix the user signed into, if it exists."""
        return paths.drive_c(self.prefixes.auth_prefix)

    # ``prefix_env``/``prefix_name`` are load-bearing: without them the
    # launcher derives the prefix from ``ctx.game_id`` ("bnet-auth") and
    # signs the user in to an empty ``prefixes/battlenet/bnet-auth`` while
    # the client lives in ``.bnet-auth``. Same token Ubisoft passes for UPC.
    AUTH_SHORTCUT = AuthShortcutSpec(
        store="battlenet",
        store_game_id="battlenet:bnet-auth",
        display_name="Battle.net",
        action_env="UNIFIDECK_BATTLENET_ACTION",
        prefix_env="UNIFIDECK_BATTLENET_PREFIX_NAME",
        prefix_name=paths.AUTH_PREFIX_NAME,
    )

    async def get_auth_shortcut_context(self) -> dict[str, Any]:
        """Payload the frontend needs to RunGame the sign-in shortcut.

        Without this the client can only be opened in Desktop Mode: in
        Gaming Mode a process with no Steam shortcut gets no gamescope
        session and its window never renders.
        """
        return await build_context(
            self._shortcut_service, self.AUTH_SHORTCUT, self._plugin_dir,
        )

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
        if drive_c is None or self._signed_out_marker.exists():
            self._cached_available = False
            return False
        from .ownership import read_licences

        # ``StoreRegistry.available()`` reads this attribute rather than
        # calling us, so a store that never sets it is never "available".
        self._cached_available = read_licences(drive_c).is_usable
        return self._cached_available

    @property
    def _signed_out_marker(self) -> Path:
        """Set by logout, cleared by a successful sign-in.

        Needed because nothing on disk distinguishes "signed in" from
        "signed out but remembered". Measured across three prefixes: the
        licence ledger AND the ``login_cache`` battle tag both survive a
        sign-out — they are a cache of the last account, which is how the
        client pre-fills the login form. Keying availability on either one
        means the store reports connected forever.

        A marker rather than deleting the prefix, because for this store the
        prefix holds the user's installed games.
        """
        return self.config.prefixes_dir_path / ".unifideck_signed_out"

    async def start_auth(self, **_kwargs: Any) -> AuthResult:
        """Open the vendor client so the user can sign in.

        The client login is the primary credential: it produces both the
        licence ledger and the cached catalog. The frontend drives this by
        RunGame-ing an auth shortcut, because a backend-spawned process has
        no gamescope session in Gaming Mode.
        """
        # Clear the signed-out marker up front. The client login happens in
        # a subprocess we do not observe, so the moment the user asks to
        # sign in is the last point we can reliably act; leaving it set
        # would make a successful sign-in still read as disconnected.
        with contextlib.suppress(OSError):
            self._signed_out_marker.unlink(missing_ok=True)
        status = inspect_prefix(self.prefixes.auth_prefix)
        if not status.usable:
            # Deliberately NOT installed here. ``AuthDispatcher.kickAndLaunch``
            # awaits this RPC *before* it RunGame-s the auth shortcut, so
            # anything slow here delays the launcher and anything that blocks
            # here stops it running at all. That is precisely what happened:
            # the installer opened a wizard, in Gaming Mode it had no
            # gamescope session to render into, this call never returned, and
            # the launcher never started — a Sign In button that did nothing.
            #
            # The install now happens behind RunGame, in
            # ``battlenet_auth_launch``, which is the rule
            # ``services/download/wrapper_signals.py`` already states: the
            # backend must not spawn the vendor client itself. Its installer
            # is no exception.
            logger.info(
                "[Battlenet] auth prefix has no client — the sign-in shortcut "
                "will install it",
            )
        return AuthResult(
            success=True,
            store=self.store_name,
            next_step="client_login",
            metadata={
                "pending": True,
                "prefix": str(self.prefixes.auth_prefix),
                "needs_bootstrap": not status.usable,
            },
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

        The *session* is a different matter from the prefix. Every game prefix
        holds a working copy of it, so without a purge the next launch opens a
        client that is still signed in and the sign-out silently did nothing.
        Only session files are removed; the games are untouched.
        """
        purged = await self.purge_session_everywhere()
        if purged:
            logger.info(
                "[Battlenet] removed %d session file(s) from the template and "
                "game prefixes", purged,
            )
        try:
            self._cache.clear("battlenet")
        except Exception:
            logger.warning("[Battlenet] cache invalidate failed during logout")
        try:
            self._signed_out_marker.parent.mkdir(parents=True, exist_ok=True)
            self._signed_out_marker.touch()
        except OSError:
            logger.warning("[Battlenet] could not record the signed-out state")
        # STORE_LOGOUT is emitted by ``StoreRegistry.auth_action`` on a
        # successful logout, which is the only path that reaches here.
        # Emitting it again would deliver the event twice.
        self._cached_available = False
        logger.info(
            "[Battlenet] signed out (prefixes untouched — they hold the games)",
        )
        return Result(success=True, store=self.store_name)

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
        self._record_families(games)
        logger.info(
            "[Battlenet] library: %d titles (%d installed, force=%s)",
            len(games),
            sum(1 for g in games if g.installed),
            force,
        )
        return games

    def _record_families(self, games: list[Game]) -> None:
        """Persist each title's ``--exec`` family code to the id map.

        The launcher runs out-of-process under the system Python and cannot
        reach the catalog, so a family it is never told is a family it can
        never use — and Battle.net's failure mode for a missing or obsolete
        family is *silent*. Doing this at sync (rather than at install) is
        what makes a title launchable without a prior install, and is the
        only writer that sees the whole library.
        """
        changed = library_mod.record_families(self.id_map, games)
        if changed:
            logger.info("[Battlenet] recorded family codes for %d title(s)", changed)

    def _collect_installed(self) -> dict[str, Any]:
        """Install state across every prefix we have recorded."""
        merged: dict[str, Any] = {}
        for prefix in self.id_map.all_prefix_paths():
            drive_c = paths.drive_c(prefix)
            if drive_c is None:
                continue
            merged.update(library_mod.read_install_state(drive_c, prefix))
        return merged

    async def install_game(
        self, game_id: str, *, install_path: str | None = None, **kwargs: Any,
    ) -> InstallResult:
        """Hand the install to the client; completion is polled elsewhere.

        ``--exec="install <FAMILY>"`` does **not** start a download — that
        was measured against the current client with a known-good family
        code. The install is a user click inside the client, exactly as it
        is for Ubisoft, so this prepares the prefix and signals the frontend
        to bring the client up.

        ``install_path`` is the storage location the user picked. The game
        installs *inside* the prefix, so placing the prefix there is the only
        thing that puts the game on that disk — and the only way the client's
        own free-space check reads the right volume.
        """
        del kwargs
        return await self._installer.install(game_id, install_path)

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
        # Last chance to keep this prefix's session. The client rotates the
        # token on every run, so a played game usually holds a NEWER one than
        # the auth prefix; deleting it uncaptured strands auth on a stale
        # token and the next install opens signed-out.
        await self.capture_before_prefix_loss(prefix)
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
