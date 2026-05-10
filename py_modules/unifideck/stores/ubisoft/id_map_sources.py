"""id_map_sources.py — Sources that feed UbisoftIdMap.

# OP-55h | py_modules/unifideck/stores/ubisoft/id_map_sources.py | Depends: (none)

The id-map can be filled from three sources, in order of authority:

1. The per-prefix Wine ``system.reg`` (and ``user.reg``) — produced by
   UPC at install time, contains the canonical numeric InstallId.
2. The UPC ``configurations`` cache (parsed by :mod:`.parser`).
3. A community-maintained text database of (numeric_id, name) pairs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import urllib.request
from typing import TYPE_CHECKING, Any

from ...core.net import ssl_ctx_permissive

if TYPE_CHECKING:
    from .id_map import UbisoftIdMap

logger = logging.getLogger(__name__)

_REGISTRY_INSTALLS_PATTERN = re.compile(
    r'\[Software\\\\Wow6432Node\\\\Ubisoft\\\\Launcher\\\\Installs\\\\(\d+)\][^\[]*?'
    r'"InstallDir"\s*=\s*"([^"]*)"',
    re.DOTALL,
)
_USER_REG_INSTALLS_PATTERN = re.compile(
    r'\[Software\\\\Ubisoft\\\\Launcher\\\\Installs\\\\(\d+)\]',
)
_STANDARD_INSTALL_PATH_MARKERS = (
    'Ubisoft Game Launcher/games/',
    'Ubisoft Game Launcher\\games\\',
)


def extract_game_id_from_registry(prefix_path: str) -> str | None:
    """Extract game ID from registry."""
    for reg_name in ('system.reg', os.path.join('pfx', 'system.reg')):
        reg_path = os.path.join(prefix_path, reg_name)
        content = read_reg_file(reg_path)
        if not content:
            continue
        game_id = scan_system_reg_installs(content)
        if game_id:
            logger.info('[Ubisoft] game ID %s from %s', game_id, reg_name)
            return game_id
        sibling = extract_id_from_user_reg_sibling(reg_path)
        if sibling:
            return sibling
    return None


def read_reg_file(reg_path: str) -> str | None:
    """Read reg file."""
    if not os.path.isfile(reg_path):
        return None
    try:
        with open(reg_path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return None


def scan_system_reg_installs(content: str) -> str | None:
    """Scan system reg installs."""
    for match in _REGISTRY_INSTALLS_PATTERN.finditer(content):
        game_id = match.group(1)
        install_dir = match.group(2).replace('\\\\', '/')
        if any(m in install_dir for m in _STANDARD_INSTALL_PATH_MARKERS):
            return game_id
    return None


def extract_id_from_user_reg_sibling(reg_path: str) -> str | None:
    """Extract ID from user reg sibling."""
    user_reg = reg_path.replace('system.reg', 'user.reg')
    content = read_reg_file(user_reg)
    if not content:
        return None
    match = _USER_REG_INSTALLS_PATTERN.search(content)
    return match.group(1) if match else None


class _IdMapSources:
    """Id map sources."""

    def __init__(self, idmap: UbisoftIdMap) -> None:
        """Initialize the instance."""
        self._idmap = idmap

    async def refresh_from_configurations(
        self, space_id: str | None = None,
    ) -> bool:
        """Refresh from configurations.

        Walks every per-game prefix, parses the UPC ``configurations``
        binary, and merges the discovered (install_id, launch_id)
        pairs into the id-map. Returns True when at least one entry
        was added or updated.
        """
        from .parser import build_id_map_from_configurations
        config = self._idmap._config
        paths = self._idmap._paths
        updated = False
        for prefix_dir in (
            [paths.get_prefix_path(space_id)] if space_id
            else config.iter_game_prefix_paths()
        ):
            cfg_path = paths.find_configurations(prefix_dir)
            if not cfg_path:
                continue
            if await self._refresh_from_path(
                cfg_path, build_id_map_from_configurations,
                f'configurations:{prefix_dir}',
            ):
                updated = True
        return updated

    async def _refresh_from_path(
        self, config_path: str, parser_fn: Any, label: str,
    ) -> bool:
        """Refresh from path."""
        try:
            mapping = await asyncio.to_thread(parser_fn, config_path)
        except Exception as e:
            logger.warning('[Ubisoft] parse %s failed: %s', label, e)
            return False
        if not mapping:
            return False
        self._idmap.update_bulk(mapping)
        logger.info(
            '[Ubisoft] merged %d entries from %s', len(mapping), label,
        )
        return True

    async def fetch_game_id_database(self) -> list[tuple[str, str]]:
        """Fetch game ID database."""
        config = self._idmap._config
        db_path = config.game_id_db_file_expanded
        if os.path.isfile(db_path):
            age = time.time() - os.path.getmtime(db_path)
            if age < config.game_id_db_max_age_seconds:
                return await asyncio.to_thread(
                    self._parse_game_id_database, db_path,
                )
        try:
            await asyncio.to_thread(
                self._download_game_id_database,
                config.game_id_db_url, db_path,
            )
        except Exception as e:
            logger.warning('[Ubisoft] game ID DB download failed: %s', e)
            if not os.path.isfile(db_path):
                return []
        return await asyncio.to_thread(
            self._parse_game_id_database, db_path,
        )

    async def lookup_game_id_by_name(self, game_name: str) -> str | None:
        """Lookup game ID by name."""
        if not game_name:
            return None
        target = self._idmap.normalize_for_matching(game_name)
        for game_id, candidate in await self.fetch_game_id_database():
            if self._idmap.normalize_for_matching(candidate) == target:
                return game_id
        return None

    @staticmethod
    def _download_game_id_database(url: str, dest_path: str) -> None:
        """Download game ID database."""
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        tmp = dest_path + '.tmp'
        ctx = ssl_ctx_permissive('ubisoft community game-id db')
        with urllib.request.urlopen(url, context=ctx, timeout=60) as resp:
            with open(tmp, 'wb') as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        os.replace(tmp, dest_path)
        logger.info('[Ubisoft] game ID database downloaded to %s', dest_path)

    @staticmethod
    def _parse_game_id_database(filepath: str) -> list[tuple[str, str]]:
        """Parse game ID database."""
        entries: list[tuple[str, str]] = []
        try:
            with open(filepath, encoding='utf-8', errors='replace') as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split(', ', 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        entries.append((parts[0], parts[1]))
        except OSError as e:
            logger.warning('[Ubisoft] game ID DB parse failed: %s', e)
        return entries
