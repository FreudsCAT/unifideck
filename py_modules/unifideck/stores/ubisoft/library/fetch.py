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
        *,
        force: bool = False,
    ) -> list[Game] | None:
        """Fetch local binaries.

        ``force`` (a force-sync) makes the unifiDB lookups (install_id list +
        uuid catalog) bypass their TTL cache and re-download.
        """
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
        owned_uuids = await self._loader.load_ownership_uuids()
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
        db_entries = await self._fetch_db_entries(force=force)
        uuid_catalog = await self._id_map.fetch_uuid_catalog(force=force)
        db_names = {
            self._id_map.normalize_for_matching(name)
            for _iid, name in db_entries
            if name
        }
        db_names |= {
            self._id_map.normalize_for_matching(name)
            for name in uuid_catalog.values()
            if name
        }
        # Backfill owned games that have no local ``configurations`` row.
        # UPC only caches configs for installed/recent titles, so the
        # owned∩config intersection is tiny (~6 of 118 owned IDs here). Two
        # complementary sources name the rest so the full owned library
        # shows: the legacy install_id list (numeric ids) and the unifiDB
        # uuid catalog (modern ids the legacy list lacks). Both flow through
        # the same dedup/DLC filters in ``build_games_from_configs``.
        id_backfill = 0
        uuid_backfill = 0
        if owned_set is not None:
            extra = self._build_backfill_configs(
                owned_set, config_by_id, configs, db_entries,
            )
            id_backfill = len(extra)
            matched_configs = matched_configs + extra
        if owned_uuids and uuid_catalog:
            extra_uuid = self._build_uuid_backfill_configs(
                owned_uuids, uuid_catalog,
            )
            uuid_backfill = len(extra_uuid)
            matched_configs = matched_configs + extra_uuid
        connect_ids = await asyncio.to_thread(self._id_map.read_connect_ids)
        games = self._builder.build_games_from_configs(
            matched_configs,
            installed,
            db_names=db_names,
            connect_ids=connect_ids,
        )
        games = await self._apply_steam_filter(games)
        logger.info(
            "[UbisoftLibrary] local binary library: %d games "
            "(%d config-matched + %d id-backfilled + %d uuid-backfilled)",
            len(games),
            len(matched_configs) - id_backfill - uuid_backfill,
            id_backfill,
            uuid_backfill,
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

    async def _fetch_db_entries(
        self,
        *,
        force: bool = False,
    ) -> list[tuple[str, str]]:
        """Community game-ID DB as ``(install_id, name)`` pairs.

        Feeds both DLC parent-name detection and the owned-game backfill.
        ``force`` bypasses the TTL cache. Degrades to an empty list when the
        database is offline or unavailable.
        """
        try:
            return await self._id_map.fetch_game_id_database(force=force)
        except Exception:
            logger.debug(
                "[UbisoftLibrary] game-ID DB unavailable",
            )
            return []

    def _build_uuid_backfill_configs(
        self,
        owned_uuids: set[str],
        uuid_catalog: dict[str, str],
    ) -> list[GameConfig]:
        """Synthesize ``GameConfig`` rows for owned product UUIDs, named via
        the unifiDB uuid catalog. These cover modern games whose install_ids
        the legacy list lacks. The UUID becomes the game's ``space_id`` (and
        thus its ``store_game_id``), so leveldb-sourced ``connect_ids`` can
        still supply a ``uplay://`` deeplink. Names colliding with an
        install_id-backfilled game collapse via the dedup in
        ``build_games_from_configs``.
        """
        from unifideck.stores.ubisoft.parser import GameConfig

        backfilled: list[GameConfig] = []
        for uuid in owned_uuids:
            name = uuid_catalog.get(uuid)
            if not name:
                continue
            synth = GameConfig()
            synth.space_id = uuid
            synth.name = name
            backfilled.append(synth)
        return backfilled

    def _build_backfill_configs(
        self,
        owned_set: set[int],
        config_by_id: dict[int, GameConfig],
        configs: list[GameConfig],
        db_entries: list[tuple[str, str]],
    ) -> list[GameConfig]:
        """Synthesize ``GameConfig`` rows for owned IDs absent from the local
        ``configurations`` cache, named via the community DB (falling back to
        any parsed config names). Owned IDs the DB can't name (modern games
        not yet in the DB) are skipped — they stay unlisted until the DB
        covers them. Edition/DLC/test noise is removed downstream by
        :meth:`_GameBuilder.build_games_from_configs`.
        """
        from unifideck.stores.ubisoft.parser import GameConfig

        id_to_name: dict[int, str] = {}
        for iid_str, name in db_entries:
            if not name:
                continue
            try:
                iid = int(iid_str)
            except (TypeError, ValueError):
                continue
            id_to_name.setdefault(iid, name)
        for cfg in configs:
            if cfg.name:
                id_to_name.setdefault(cfg.install_id, cfg.name)
        backfilled: list[GameConfig] = []
        for oid in owned_set:
            if oid in config_by_id:
                continue
            resolved = id_to_name.get(oid)
            if not resolved:
                continue
            synth = GameConfig()
            synth.install_id = oid
            synth.launch_id = oid
            synth.name = resolved
            backfilled.append(synth)
        return backfilled

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
