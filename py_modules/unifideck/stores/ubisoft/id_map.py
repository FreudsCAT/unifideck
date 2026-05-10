"""id_map.py — space_id ↔ install_id / launch_id cache.

# OP-55g | py_modules/unifideck/stores/ubisoft/id_map.py | Depends: OP-04a

Persistent JSON cache mapping UPC ``space_id`` UUIDs to the numeric
``install_id`` / ``launch_id`` plus optional metadata (game name,
ubisoftConnectGameId). Sources of truth for this map (registry, config
binary, community DB) live in :mod:`.id_map_sources`.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .config import UbisoftConfig
from .id_map_sources import (
    _IdMapSources,
    extract_game_id_from_registry as _extract_game_id_from_registry,
)
from .paths import UbisoftPrefixPaths

logger = logging.getLogger(__name__)
_STEAM_TITLE_PREFIXES_TO_SKIP = (
    'Proton', 'Steam Linux Runtime', 'Steamworks',
)


class UbisoftIdMap:
    """Ubisoft ID map."""

    def __init__(
        self, config: UbisoftConfig, paths: UbisoftPrefixPaths,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths
        self._cache: dict[str, dict[str, Any]] = {}
        self._sources = _IdMapSources(self)
        self._load()

    def _load(self) -> None:
        """Load."""
        path = self._config.id_map_file_expanded
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._cache = data
                logger.info(
                    '[Ubisoft] loaded id_map (%d entries)', len(data),
                )
        except (OSError, json.JSONDecodeError) as e:
            logger.warning('[Ubisoft] failed to load id_map: %s', e)
            self._cache = {}

    def _save(self) -> None:
        """Save."""
        path = self._config.id_map_file_expanded
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, indent=2)
            os.replace(tmp, path)
        except OSError as e:
            logger.warning('[Ubisoft] failed to save id_map: %s', e)

    def resolve_install_id(self, space_id: str) -> str | None:
        """Resolve install ID."""
        entry = self._cache.get(space_id, {})
        if 'ubisoftconnect_game_id' in entry:
            return entry.get('ubisoftconnect_game_id')
        return entry.get('install_id')

    def resolve_launch_id(self, space_id: str) -> str | None:
        """Resolve launch ID."""
        entry = self._cache.get(space_id, {})
        if 'ubisoftconnect_game_id' in entry:
            return entry.get('ubisoftconnect_game_id')
        return entry.get('launch_id')

    def update(
        self, space_id: str, install_id: str, launch_id: str,
    ) -> None:
        """Update."""
        existing = self._cache.get(space_id, {})
        existing.update({
            'install_id': install_id,
            'launch_id': launch_id,
        })
        self._cache[space_id] = existing
        self._save()

    def update_bulk(self, mapping: dict[str, dict[str, Any]]) -> None:
        """Update bulk."""
        if not mapping:
            return
        for space_id, fields in mapping.items():
            existing = self._cache.get(space_id, {})
            existing.update(fields)
            self._cache[space_id] = existing
        self._save()

    def merge_entry(
        self, space_id: str, fields: dict[str, Any],
    ) -> bool:
        """Merge entry. Returns True when something changed."""
        if not space_id or not fields:
            return False
        existing = self._cache.get(space_id, {})
        changed = False
        for key, value in fields.items():
            if value in (None, ''):
                continue
            if existing.get(key) != value:
                existing[key] = value
                changed = True
        if changed:
            self._cache[space_id] = existing
            self._save()
        return changed

    def get_entry(self, space_id: str) -> dict[str, Any]:
        """Get entry."""
        return dict(self._cache.get(space_id, {}))

    def in_cache(self, space_id: str) -> bool:
        """In cache."""
        return space_id in self._cache

    async def refresh_from_configurations(
        self, space_id: str | None = None,
    ) -> bool:
        """Refresh from configurations."""
        return await self._sources.refresh_from_configurations(space_id)

    async def fetch_game_id_database(self) -> list[tuple[str, str]]:
        """Fetch game ID database."""
        return await self._sources.fetch_game_id_database()

    async def lookup_game_id_by_name(self, game_name: str) -> str | None:
        """Lookup game ID by name."""
        return await self._sources.lookup_game_id_by_name(game_name)

    @staticmethod
    def extract_game_id_from_registry(prefix_path: str) -> str | None:
        """Extract game ID from registry."""
        return _extract_game_id_from_registry(prefix_path)

    @staticmethod
    def get_steam_library_titles() -> set[str]:
        """Best-effort scrape of Steam's libraryfolders.vdf to give the
        steam-filter cross-ref a list of installed Steam titles. Returns
        an empty set when Steam isn't installed or the VDF can't be
        parsed.
        """
        result: set[str] = set()
        for steamapps in (
            os.path.expanduser('~/.steam/steam/steamapps'),
            os.path.expanduser('~/.local/share/Steam/steamapps'),
        ):
            if not os.path.isdir(steamapps):
                continue
            try:
                manifests = [
                    f for f in os.listdir(steamapps)
                    if f.startswith('appmanifest_') and f.endswith('.acf')
                ]
            except OSError:
                continue
            for manifest in manifests:
                try:
                    with open(
                        os.path.join(steamapps, manifest), encoding='utf-8', errors='replace',
                    ) as f:
                        for line in f:
                            m = re.search(r'"name"\s+"([^"]+)"', line)
                            if not m:
                                continue
                            name = m.group(1).strip()
                            if not name:
                                continue
                            if any(
                                name.startswith(prefix)
                                for prefix in _STEAM_TITLE_PREFIXES_TO_SKIP
                            ):
                                break
                            result.add(name)
                            break
                except OSError:
                    continue
        return result

    @staticmethod
    def _normalize_for_matching(name: str) -> str:
        """Normalize for matching."""
        if not name:
            return ''
        normalised = name.lower().replace('_', ' ')
        normalised = re.sub(r"[®™©''\-:.,!?()\"']", '', normalised)
        return ' '.join(normalised.split())

    def normalize_for_matching(self, name: str) -> str:
        """Normalize for matching."""
        return self._normalize_for_matching(name)
