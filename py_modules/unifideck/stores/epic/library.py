"""library.py — Read the owned + installed Epic library via legendary.

# OP-48c | py_modules/unifideck/stores/epic/library.py | Depends: OP-48a

``read_installed_map`` is hot — invoked during every library scan and
during install/uninstall finalisation — so we cache its result for
``installed_ttl`` seconds (30 by default). ``read_owned_games`` is
cold; one call per library sync.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ...core.types import Game
from .filter import should_filter_epic_item

logger = logging.getLogger(__name__)
DEFAULT_INSTALLED_TTL = 30


class EpicLibraryReader:
    """Epic library reader."""

    def __init__(
        self,
        cli_path: str | None,
        library_timeout: int = 30,
        installed_ttl: int = DEFAULT_INSTALLED_TTL,
    ) -> None:
        """Initialize the instance."""
        self._cli_path = cli_path
        self._library_timeout = library_timeout
        self._installed_ttl = installed_ttl
        self._installed_cache: dict[str, dict[str, Any]] | None = None
        self._installed_cache_at: float = 0.0

    async def read_owned_games(self) -> list[Game]:
        """Read owned games."""
        if not self._cli_path:
            return []
        data = await self._run_legendary_json(['list', '--json'])
        if not isinstance(data, list):
            return []
        installed = await self.read_installed_map()
        games: list[Game] = []
        filtered = 0
        for entry in data:
            if not isinstance(entry, dict):
                continue
            if should_filter_epic_item(entry):
                filtered += 1
                continue
            app_name = str(entry.get('app_name') or '')
            if not app_name:
                continue
            title = str(entry.get('app_title') or app_name)
            install_info = installed.get(app_name) or {}
            games.append(
                Game(
                    store='epic',
                    game_id=app_name,
                    title=title,
                    installed=bool(install_info),
                    install_path=str(install_info.get('install_path', '')),
                ),
            )
        logger.info(
            '[epic_library] %d owned (filtered %d UE/asset/mod)',
            len(games), filtered,
        )
        return games

    async def read_installed_map(
        self, force_refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        """Read installed map."""
        now = time.time()
        if (
            not force_refresh
            and self._installed_cache is not None
            and (now - self._installed_cache_at) < self._installed_ttl
        ):
            return self._installed_cache
        loaded = await self._load_installed_from_cli()
        self._installed_cache = loaded
        self._installed_cache_at = now
        return loaded

    async def _load_installed_from_cli(self) -> dict[str, dict[str, Any]]:
        """Load installed from CLI."""
        if not self._cli_path:
            return {}
        data = await self._run_legendary_json(
            ['list-installed', '--json'],
        )
        if not isinstance(data, list):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            app_name = entry.get('app_name')
            if isinstance(app_name, str) and app_name:
                out[app_name] = entry
        return out

    def invalidate_installed_cache(self) -> None:
        """Invalidate installed cache."""
        self._installed_cache = None
        self._installed_cache_at = 0.0

    async def _run_legendary_json(self, args: list[str]) -> Any:
        """Run LEGENDARY JSON."""
        if not self._cli_path:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._library_timeout,
                )
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                logger.warning(
                    '[epic_library] %s timed out', ' '.join(args),
                )
                return None
        except OSError as e:
            logger.warning('[epic_library] spawn failed: %s', e)
            return None
        if proc.returncode != 0:
            logger.warning(
                '[epic_library] %s rc=%s err=%s',
                ' '.join(args), proc.returncode,
                stderr.decode('utf-8', errors='replace')[:200],
            )
            return None
        try:
            return json.loads(stdout.decode('utf-8', errors='replace'))
        except json.JSONDecodeError as e:
            logger.warning('[epic_library] json decode failed: %s', e)
            return None


def merge_install_status(
    owned: list[Game], installed: dict[str, dict[str, Any]],
) -> list[Game]:
    """Merge install status."""
    if not installed:
        return owned
    out: list[Game] = []
    for game in owned:
        info = installed.get(game.game_id)
        if info:
            game.installed = True
            game.install_path = str(info.get('install_path', ''))
        out.append(game)
    return out
