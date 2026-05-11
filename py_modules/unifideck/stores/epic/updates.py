"""updates.py — Detect & apply updates via ``legendary``.

# OP-48e | py_modules/unifideck/stores/epic/updates.py | Depends: OP-48a

Legendary's ``--json`` flag drops the ``update_available`` field due
to an upstream bug, so :meth:`check_for_updates` parses the plaintext
``list-installed --check-updates`` output instead. Game-size lookups
are cached per game for ``size_cache_ttl`` seconds (default 300).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ...core.types import Events, InstallResult
from ...event_bus.event_bus import EventBus
from .legendary import fetch_info
from .library import EpicLibraryReader

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[float], Awaitable[None]] | None


class EpicUpdateChecker:
    """Epic update checker."""

    def __init__(
        self,
        bus: EventBus,
        cli_path: str | None,
        library: EpicLibraryReader,
        list_updates_timeout: int,
        size_cache_ttl: int,
        info_timeout: float,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._cli_path = cli_path
        self._library = library
        self._list_updates_timeout = list_updates_timeout
        self._size_cache_ttl = size_cache_ttl
        self._info_timeout = info_timeout
        self._size_cache: dict[str, tuple[int, float]] = {}

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        if not self._cli_path:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, 'list-installed', '--check-updates',
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
                logger.warning('[epic_updates] list-installed timed out')
                return []
        except OSError as e:
            logger.warning('[epic_updates] spawn failed: %s', e)
            return []
        if proc.returncode != 0:
            logger.warning(
                '[epic_updates] rc=%s err=%s',
                proc.returncode,
                stderr.decode('utf-8', errors='replace')[:200],
            )
            return []
        return self._parse_update_output(
            stdout.decode('utf-8', errors='replace'),
        )

    @staticmethod
    def _parse_update_output(text: str) -> list[str]:
        """Parse update output."""
        updates: list[str] = []
        current_app: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('*') and 'App name:' in stripped:
                try:
                    current_app = stripped.split('App name:')[1].split('|')[0].strip()
                except IndexError:
                    current_app = None
            elif stripped.startswith('-> Update available!') and current_app:
                updates.append(current_app)
                current_app = None
        return updates

    async def update_game(
        self,
        game_id: str,
        installer: Any,
        progress_cb: ProgressCallback = None,
    ) -> InstallResult:
        """Update game."""
        if not self._cli_path:
            return InstallResult(
                success=False, store='epic', game_id=game_id,
                error='legendary_not_found',
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, 'update', game_id,
                '--with-dlcs', '--yes',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await self._stream_update_output(proc, game_id)
            rc = await proc.wait()
        except OSError as e:
            return InstallResult(
                success=False, store='epic', game_id=game_id,
                error=f'spawn_failed:{e}',
            )
        self._library.invalidate_installed_cache()
        if rc != 0:
            return InstallResult(
                success=False, store='epic', game_id=game_id,
                error=f'legendary_rc:{rc}',
            )
        return InstallResult(
            success=True, store='epic', game_id=game_id,
        )

    async def _stream_update_output(self, proc: Any, game_id: str) -> None:
        """Stream update output."""
        if proc.stdout is None:
            return
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line_str = line.decode('utf-8', errors='replace').strip()
            if not line_str:
                continue
            await self._bus.emit(
                Events.DOWNLOAD_PROGRESS,
                store='epic', game_id=game_id, line=line_str,
            )

    async def get_game_size(self, game_id: str) -> int | None:
        """Get game size."""
        now = time.time()
        cached = self._size_cache.get(game_id)
        if cached and (now - cached[1]) < self._size_cache_ttl:
            return cached[0]
        size = await self._load_game_size_from_cli(game_id)
        if size is not None:
            self._size_cache[game_id] = (size, now)
        return size

    async def _load_game_size_from_cli(self, game_id: str) -> int | None:
        """Load game size from CLI."""
        info = await self._fetch_info(game_id)
        if not isinstance(info, dict):
            return None
        manifest = info.get('manifest') or {}
        if not isinstance(manifest, dict):
            return None
        size = manifest.get('download_size')
        try:
            return int(size) if size is not None else None
        except (TypeError, ValueError):
            return None

    async def _fetch_info(self, game_id: str) -> dict[str, Any] | None:
        """Fetch info."""
        if not self._cli_path:
            return None
        return await fetch_info(
            self._cli_path, game_id,
            timeout=self._info_timeout,
            log_prefix='[epic_updates]',
        )
