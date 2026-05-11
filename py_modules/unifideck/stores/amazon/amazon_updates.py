"""amazon_updates.py — Update detection + size lookup via ``nile``.

# OP-49e | py_modules/unifideck/stores/amazon/amazon_updates.py | Depends: OP-49a
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from ...event_bus.event_bus import EventBus
from .amazon_library import AmazonLibraryReader

logger = logging.getLogger(__name__)


class AmazonUpdateChecker:
    """Amazon update checker."""

    def __init__(
        self,
        bus: EventBus,
        cli_path: str | None,
        library: AmazonLibraryReader,
        list_updates_timeout: int,
        get_size_timeout: int,
        default_install_root: str,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._cli_path = cli_path
        self._library = library
        self._list_updates_timeout = list_updates_timeout
        self._get_size_timeout = get_size_timeout
        self._default_install_root = default_install_root

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        if not self._cli_path:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, 'list-updates', '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._list_updates_timeout,
                )
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                logger.warning('[amazon_updates] list-updates timed out')
                return []
        except OSError as e:
            logger.warning('[amazon_updates] spawn failed: %s', e)
            return []
        if proc.returncode != 0:
            logger.debug(
                '[amazon_updates] rc=%s err=%s',
                proc.returncode,
                stderr.decode('utf-8', errors='replace')[:200],
            )
            return []
        try:
            data = json.loads(stdout.decode('utf-8', errors='replace'))
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        out: list[str] = []
        for entry in data:
            if isinstance(entry, dict):
                game_id = entry.get('id') or entry.get('game_id')
                if isinstance(game_id, str) and game_id:
                    out.append(game_id)
            elif isinstance(entry, str) and entry:
                out.append(entry)
        return out

    async def get_game_size(self, game_id: str) -> int | None:
        """Get game size."""
        if not self._cli_path:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, 'install', game_id, '--info', '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._get_size_timeout,
                )
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                return None
        except OSError as e:
            logger.debug('[amazon_updates] get_game_size spawn: %s', e)
            return None
        if proc.returncode != 0:
            return None
        try:
            data = json.loads(stdout.decode('utf-8', errors='replace'))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        for key in ('download_size', 'size', 'total_size'):
            value = data.get(key)
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return None

    async def resolve_current_base_path(self, game_id: str) -> str:
        """Resolve current base path."""
        installed = await self._library.read_installed_ids()
        info = installed.get(game_id) or {}
        path = info.get('path') or info.get('install_path')
        if isinstance(path, str) and path:
            parent = str(Path(path).parent)
            if os.path.isdir(parent):
                return parent
        return os.path.expanduser(self._default_install_root)


_: Any = None
