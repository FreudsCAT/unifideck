"""library.py — Public ``GOGLibrary`` surface.

# OP-50c | py_modules/unifideck/stores/gog/library.py | Depends: OP-50a

Fetches the user's owned-game list from GOG's REST API, walks the
local download dir for install markers, and stitches the two together
into a list of :class:`Game` records.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...core.types import Game
from .config import GOGConfig
from .http import build_ssl_context, fetch_json_get
from .library_migration import _MarkerMigration
from .tokens import GOGTokenManager

logger = logging.getLogger(__name__)
_INSTALL_MARKER = '.unifideck-id'
_GOG_LIBRARY_TIMEOUT_S = 15.0


class GOGLibrary:
    """GOG library."""

    def __init__(
        self,
        config: GOGConfig,
        tokens: GOGTokenManager,
        exe_finder: Callable[[str], str | None] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._tokens = tokens
        self._exe_finder = exe_finder
        self._migration = _MarkerMigration(self)

    def migrate_old_markers(self) -> dict[str, int]:
        """Migrate old markers."""
        return self._migration.migrate_old_markers()

    async def is_available(self) -> bool:
        """Is available."""
        if not await self._tokens.refresh_if_stale():
            return False
        try:
            count = await self._probe_userdata()
        except Exception as e:
            logger.debug('[GOGLibrary] probe failed: %s', e)
            return False
        return count >= 0

    async def _probe_userdata(self) -> int:
        """Probe userdata."""
        if not self._config.api_gog_url or not self._tokens.access_token:
            return -1
        data = await self._fetch_json(
            f'{self._config.api_gog_url}/user/data/games',
        )
        if not isinstance(data, dict):
            return -1
        owned = data.get('owned')
        return len(owned) if isinstance(owned, list) else 0

    async def fetch_library(self) -> list[Game]:
        """Fetch library."""
        if not await self._tokens.refresh_if_stale():
            return []
        owned_ids = await self._owned_game_ids()
        installed_map = self._installed_map()
        out: list[Game] = []
        for game_id in owned_ids:
            slug = await self.get_game_slug(game_id) or ''
            info = installed_map.get(game_id, {})
            installed = bool(info)
            install_path = info.get('install_path', '')
            title = info.get('title') or slug.replace('_', ' ').title()
            out.append(
                Game(
                    store='gog',
                    game_id=str(game_id),
                    title=title,
                    installed=installed,
                    install_path=install_path,
                ),
            )
        return out

    async def _owned_game_ids(self) -> list[str]:
        """Owned game IDs."""
        data = await self._fetch_json(
            f'{self._config.api_gog_url}/user/data/games',
        )
        if not isinstance(data, dict):
            return []
        owned = data.get('owned')
        if not isinstance(owned, list):
            return []
        return [str(g) for g in owned]

    async def get_game_slug(self, game_id: str) -> str | None:
        """Get game slug."""
        url = f'{self._config.api_gog_url}/products/{game_id}'
        data = await self._fetch_json(url)
        if isinstance(data, dict):
            slug = data.get('slug')
            if isinstance(slug, str) and slug:
                return slug
        return None

    def get_installed(self) -> list[str]:
        """Get installed."""
        return list(self._installed_map().keys())

    def get_installed_game_info(
        self, game_id: str,
    ) -> dict[str, str | None] | None:
        """Get installed game info."""
        info = self._installed_map().get(game_id)
        return info if info else None

    def _installed_map(self) -> dict[str, dict[str, str]]:
        """Installed map."""
        download_dir = os.path.expanduser(self._config.download_dir)
        out: dict[str, dict[str, str]] = {}
        if not os.path.isdir(download_dir):
            return out
        for entry in sorted(os.listdir(download_dir)):
            game_dir = os.path.join(download_dir, entry)
            if not os.path.isdir(game_dir):
                continue
            game_id = self._read_marker(game_dir)
            if not game_id and self._has_goggame_info(game_dir, ''):
                game_id = self._infer_game_id_from_goggame(game_dir)
            if not game_id:
                continue
            out[game_id] = {
                'install_path': game_dir,
                'title': entry,
                'executable': self._resolve_exe(game_dir) or '',
            }
        return out

    @staticmethod
    def _read_marker(game_dir: str) -> str | None:
        """Read marker."""
        marker = os.path.join(game_dir, _INSTALL_MARKER)
        if not os.path.isfile(marker):
            return None
        try:
            content = Path(marker).read_text(encoding='utf-8').strip()
        except OSError:
            return None
        if content.startswith('{'):
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return None
            gid = data.get('game_id') if isinstance(data, dict) else None
            return str(gid) if gid else None
        line = content.split('\n', 1)[0].strip()
        return line if line.isdigit() else None

    @staticmethod
    def _has_goggame_info(game_dir: str, game_id: str) -> bool:
        """Has goggame info."""
        try:
            for name in os.listdir(game_dir):
                if name.startswith('goggame-') and name.endswith('.info'):
                    if not game_id or f'goggame-{game_id}.info' == name:
                        return True
        except OSError:
            return False
        return False

    def _infer_game_id_from_goggame(self, game_dir: str) -> str | None:
        """Infer game id from goggame info filename."""
        try:
            for name in os.listdir(game_dir):
                if name.startswith('goggame-') and name.endswith('.info'):
                    return name[len('goggame-'):-len('.info')]
        except OSError:
            pass
        return None

    def _resolve_exe(self, install_path: str) -> str | None:
        """Resolve exe."""
        if self._exe_finder is None:
            return None
        try:
            return self._exe_finder(install_path)
        except Exception as e:
            logger.debug('[GOGLibrary] exe resolve: %s', e)
            return None

    async def _fetch_json(
        self, url: str, headers: dict[str, str] | None = None,
    ) -> Any | None:
        """Fetch JSON."""
        return await fetch_json_get(
            url,
            bearer=self._tokens.access_token,
            user_agent=self._config.user_agent,
            timeout=_GOG_LIBRARY_TIMEOUT_S,
            extra_headers=headers,
            log_prefix='[GOGLibrary]',
        )


_ = urllib.parse
_ = build_ssl_context
