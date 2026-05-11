"""fetch.py — Drive the data-loader + game-builder pipeline.

# OP-57b | py_modules/unifideck/stores/ubisoft/library/fetch.py | Depends: OP-55a
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ....core.types import Game
from .data_loader import _DataLoader
from .game_builder import _GameBuilder

if TYPE_CHECKING:
    from ..config import UbisoftConfig
    from ..id_map import UbisoftIdMap
    from ..parser import GameConfig
    from ..paths import UbisoftPrefixPaths
    ParseConfigurationsFn = Callable[[str], list[GameConfig]]
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
        self._data_loader = _DataLoader(config=config, paths=paths)
        self._builder = _GameBuilder(config=config, id_map=id_map)

    async def fetch_local_binaries(
        self, installed: dict[str, Any],
    ) -> list[Game] | None:
        """Fetch local binaries."""
        parser_pair = self._import_ubisoft_parser()
        if parser_pair is None:
            return None
        parse_configurations, parse_ownership = parser_pair
        configs = await self._data_loader.load_configurations(
            parse_configurations,
        )
        if configs is None:
            return None
        owned_set = await self._data_loader.load_ownership_set(parse_ownership)
        config_by_id = self._builder.build_config_lookup(configs)
        matched = self._builder.cross_reference_ownership(
            configs, config_by_id, owned_set,
        )
        matched = self._builder.apply_steam_filter(matched)
        return self._builder.build_games_from_configs(matched, installed)

    @staticmethod
    def _import_ubisoft_parser() -> tuple[
        ParseConfigurationsFn, ParseOwnershipFn,
    ] | None:
        """Import UBISOFT parser."""
        try:
            from ..parser import parse_configurations, parse_ownership
        except Exception as e:
            logger.warning('[Ubisoft.library] parser import failed: %s', e)
            return None
        return parse_configurations, parse_ownership
