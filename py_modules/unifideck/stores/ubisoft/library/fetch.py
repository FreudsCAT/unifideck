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

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Game

from .data_loader import _DataLoader
from .game_builder import _GameBuilder

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
        config_by_id = self._builder.build_config_lookup(configs)
        matched_configs = self._builder.cross_reference_ownership(
            configs,
            config_by_id,
            owned_set,
            installed,
        )
        matched_configs = self._builder.apply_steam_filter(
            matched_configs,
        )
        games = self._builder.build_games_from_configs(
            matched_configs,
            installed,
        )
        logger.info(
            "[UbisoftLibrary] local binary library: %d games (from %d matched configs)",
            len(games),
            len(matched_configs),
        )
        return games

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
