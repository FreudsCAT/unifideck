"""updates.py — Detect & apply GOG content updates via gogdl.

# OP-50g | py_modules/unifideck/stores/gog/updates.py | Depends: OP-50a
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...core.types import Result
from .config import GOGConfig
from .http import fetch_json_get
from .tokens import GOGTokenManager

logger = logging.getLogger(__name__)
_CONTENT_SYSTEM_URL_TEMPLATE = (
    'https://content-system.gog.com/products/{game_id}/os/windows/'
    'builds?generation=2'
)
_UPDATE_CHECK_TIMEOUT_S = 10.0


class GOGUpdatesChecker:
    """GOG updates checker."""

    def __init__(
        self,
        config: GOGConfig,
        tokens: GOGTokenManager,
        gogdl_bin: str,
        get_installed_ids: Callable[[], list[str]],
        resolve_install_info: Callable[[str], dict[str, str | None] | None],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._tokens = tokens
        self._gogdl_bin = gogdl_bin
        self._get_installed_ids = get_installed_ids
        self._resolve_install_info = resolve_install_info

    @staticmethod
    def get_local_build_id(install_path: str, game_id: str) -> str | None:
        """Get local build ID."""
        if not install_path:
            return None
        candidates = [
            os.path.join(install_path, f'goggame-{game_id}.info'),
            os.path.join(install_path, 'game', f'goggame-{game_id}.info'),
        ]
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                data = json.loads(Path(path).read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            build = data.get('buildId') if isinstance(data, dict) else None
            if isinstance(build, (str, int)):
                return str(build)
        return None

    async def check_for_game_update(self, game_id: str) -> bool | None:
        """Check for game update."""
        info = self._resolve_install_info(game_id) or {}
        install_path = info.get('install_path') if isinstance(info, dict) else None
        if not install_path:
            return None
        local = self.get_local_build_id(install_path, game_id)
        if not local:
            return None
        remote = await self._fetch_remote_build_id(game_id)
        if not remote:
            return None
        return remote != local

    async def _fetch_remote_build_id(self, game_id: str) -> str | None:
        """Fetch remote build ID."""
        url = _CONTENT_SYSTEM_URL_TEMPLATE.format(game_id=game_id)
        data = await fetch_json_get(
            url,
            user_agent=self._config.user_agent,
            timeout=_UPDATE_CHECK_TIMEOUT_S,
            log_prefix='[GOGUpdates]',
        )
        if not isinstance(data, dict):
            return None
        items = data.get('items')
        if isinstance(items, list) and items:
            build = items[0].get('build_id') if isinstance(items[0], dict) else None
            return str(build) if build else None
        return None

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        out: list[str] = []
        for game_id in self._get_installed_ids():
            try:
                needs = await self.check_for_game_update(game_id)
            except Exception as e:
                logger.debug(
                    '[GOGUpdates] check %s: %s', game_id, e,
                )
                continue
            if needs:
                out.append(game_id)
        return out

    async def update_game(
        self, game_id: str, install_path: str | None = None,
    ) -> Result:
        """Update game."""
        path = self._update_resolve_path(game_id, install_path)
        if not path[1]:
            return Result(success=False, error='no_install_path')
        if not self._gogdl_bin:
            return Result(success=False, error='gogdl_not_found')
        if not await self._tokens.refresh_if_stale():
            return Result(success=False, error='no_tokens')
        proc = await self._update_spawn_gogdl(game_id, path[1])
        if proc is None:
            return Result(success=False, error='gogdl_spawn_failed')
        await self._update_drain_output(proc)
        return await self._update_finalize(proc, game_id)

    def _update_resolve_path(
        self, game_id: str, install_path: str | None,
    ) -> tuple:
        """Update resolve path."""
        if install_path:
            return game_id, install_path
        info = self._resolve_install_info(game_id) or {}
        return game_id, (info.get('install_path') if isinstance(info, dict) else None)

    async def _update_spawn_gogdl(
        self, game_id: str, install_path: str,
    ) -> Any | None:
        """Update spawn GOGDL."""
        try:
            async with self._tokens.gogdl_credentials() as env:
                return await asyncio.create_subprocess_exec(
                    self._gogdl_bin, 'update',
                    game_id, '--path', install_path,
                    env={**env},
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
        except OSError as e:
            logger.warning('[GOGUpdates] spawn: %s', e)
            return None

    @staticmethod
    async def _update_drain_output(proc: Any) -> None:
        """Update drain output."""
        if proc.stdout is None:
            return
        while True:
            line = await proc.stdout.readline()
            if not line:
                break

    @staticmethod
    async def _update_finalize(proc: Any, game_id: str) -> Result:
        """Update finalize."""
        rc = await proc.wait()
        if rc != 0:
            return Result(success=False, error=f'gogdl_rc:{rc}')
        return Result(success=True, data={'game_id': game_id})
