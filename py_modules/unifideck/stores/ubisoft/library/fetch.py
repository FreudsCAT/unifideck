"""
Fetch the owned-games catalog from the UPC user data.

OP-57b | py_modules/unifideck/stores/ubisoft/library/fetch.py

``_LibraryFetch`` reads the UPC catalog from the user's Wine prefix
(``ownership`` and ``configurations`` directories) and returns the
parsed owned-games list. Delegates to ``parser.py`` and
``parser_binary.py`` for the actual decoding.

Errors during read are surfaced as empty results — the caller will fall
back to "installed games only" mode if the owned list can't be read.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Game

from .data_loader import _DataLoader
from .game_builder import _GameBuilder
from .steam_filter import apply_steam_owned_filter, load_steam_owned_titles

if TYPE_CHECKING:
    from unifideck.stores.ubisoft.config import UbisoftConfig
    from unifideck.stores.ubisoft.id_map import UbisoftIdMap

    # GameConfig is used in the ``ParseConfigurationsFn`` alias just
    # below as a string forward-ref. flake8 can't see through string
    # annotations so it flags F401 — silenced explicitly.
    from unifideck.stores.ubisoft.parser import GameConfig
    from unifideck.stores.ubisoft.paths import UbisoftPrefixPaths
ParseConfigurationsFn = Callable[[str], "list[GameConfig]"]
ParseOwnershipFn = Callable[[str], list[int]]
logger = logging.getLogger(__name__)


class _LibraryFetcher:
    """Library fetcher."""

    def __init__(
        self,
        config: UbisoftConfig,
        paths: UbisoftPrefixPaths,
        id_map: UbisoftIdMap,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths
        self._id_map = id_map
        self._loader = _DataLoader(config=config, paths=paths)
        self._builder = _GameBuilder(
            config=config,
            id_map=id_map,
        )

    async def fetch_local_binaries(
        self,
        installed: dict[str, Any],
    ) -> list[Game] | None:
        """Fetch local binaries."""
        parser_funcs = self._import_ubisoft_parser()
        if parser_funcs is None:
            return None
        parse_configurations, parse_ownership = parser_funcs
        configs = await self._loader.load_configurations(
            parse_configurations,
        )
        if not configs:
            return None
        owned_set = await self._loader.load_ownership_set(
            parse_ownership,
        )
        if owned_set is None:
            # get_library is auth-gated upstream, so reaching here means
            # we ARE signed in but UPC hasn't written its ownership cache
            # yet (it can lag the credential capture by a few seconds, or
            # the user closed UPC before it finished syncing). We fall
            # back to installed-only (anti-phantom) and surface the state
            # so a "signed in but library looks empty" report is
            # diagnosable; the next refresh picks up the cache.
            logger.warning(
                "[UbisoftLibrary] authenticated but UPC ownership cache "
                "absent — UPC may still be syncing; showing installed-only "
                "until the next library refresh",
            )
        config_by_id = self._builder.build_config_lookup(configs)
        matched_configs = self._builder.cross_reference_ownership(
            configs,
            config_by_id,
            owned_set,
            installed,
        )
        db_names = await self._fetch_db_names()
        connect_ids = await asyncio.to_thread(self._id_map.read_connect_ids)
        games = self._builder.build_games_from_configs(
            matched_configs,
            installed,
            db_names=db_names,
            connect_ids=connect_ids,
        )
        games = await self._apply_steam_filter(games)
        logger.info(
            "[UbisoftLibrary] local binary library: %d games (from %d matched configs)",
            len(games),
            len(matched_configs),
        )
        return games

    async def _apply_steam_filter(
        self,
        games: list[Game],
    ) -> list[Game]:
        """Hide games already owned on Steam (when enabled).

        Gated by ``filter_steam_linked``; the (blocking) Steam library
        scan runs off the event loop. A Steam-owned Ubisoft title can't
        launch via ``uplay://`` so its shortcut would be a dead end —
        see :mod:`.steam_filter`.
        """
        if not self._config.filter_steam_linked:
            return games
        steam_titles = await asyncio.to_thread(load_steam_owned_titles)
        filtered, _hidden = apply_steam_owned_filter(games, steam_titles)
        return filtered

    async def _fetch_db_names(self) -> set[str]:
        """Normalised community game-ID DB names for DLC parent detection.

        Degrades to an empty set when the database is offline or
        unavailable — separator dedup then relies on the owned-title
        set alone (see :meth:`_GameBuilder._is_dlc_by_separator`).
        """
        try:
            entries = await self._id_map.fetch_game_id_database()
        except Exception:
            logger.debug(
                "[UbisoftLibrary] game-ID DB unavailable for dedup",
            )
            return set()
        return {
            self._id_map.normalize_for_matching(name)
            for _install_id, name in entries
            if name
        }

    @staticmethod
    def _import_ubisoft_parser() -> (
        tuple[ParseConfigurationsFn, ParseOwnershipFn] | None
    ):
        """Import UBISOFT parser."""
        try:
            from unifideck.stores.ubisoft.parser import (
                parse_configurations,
                parse_ownership,
            )
        except ImportError:
            logger.exception("[UbisoftLibrary] ubisoft_parser unavailable")
            return None
        return parse_configurations, parse_ownership
