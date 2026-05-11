"""dlc.py — Probe / install DLC via gogdl.

# OP-50f | py_modules/unifideck/stores/gog/dlc.py | Depends: OP-50c
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ...core.types import Result
from .config import GOGConfig
from .http import fetch_json_get
from .tokens import GOGTokenManager

logger = logging.getLogger(__name__)
_LANGUAGE_FALLBACK = ['en-US']
_LANG_PROBE_TIMEOUT_S = 30.0


class GOGDlcManager:
    """GOG DLC manager."""

    def __init__(
        self,
        config: GOGConfig,
        tokens: GOGTokenManager,
        gogdl_bin: str,
        locale_fn: Callable[[], str],
        resolve_install_path: Callable[[str], dict[str, str | None] | None],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._tokens = tokens
        self._gogdl_bin = gogdl_bin
        self._locale_fn = locale_fn
        self._resolve_install_path = resolve_install_path

    async def get_game_dlcs(self, game_id: str) -> list[dict[str, Any]]:
        """Get game DLCs."""
        if not self._config.api_gog_url:
            return []
        url = f'{self._config.api_gog_url}/products/{game_id}?expand=expanded_dlcs'
        data = await self._http_get_json(url, bearer=self._tokens.access_token)
        if not isinstance(data, dict):
            return []
        expanded = data.get('expanded_dlcs') or []
        if not isinstance(expanded, list):
            return []
        out: list[dict[str, Any]] = []
        for dlc in expanded:
            if not isinstance(dlc, dict):
                continue
            out.append({
                'id': str(dlc.get('id', '')),
                'title': dlc.get('title', ''),
                'slug': dlc.get('slug', ''),
            })
        return out

    async def get_available_languages(self, game_id: str) -> list[str]:
        """Get available languages."""
        stdout = await self._spawn_lang_probe(game_id)
        if stdout is None:
            return list(_LANGUAGE_FALLBACK)
        languages = self._parse_languages_from_info(stdout)
        return languages or list(_LANGUAGE_FALLBACK)

    async def _spawn_lang_probe(self, game_id: str) -> bytes | None:
        """Spawn lang probe."""
        if not self._gogdl_bin:
            return None
        try:
            async with self._tokens.gogdl_credentials() as env:
                proc = await asyncio.create_subprocess_exec(
                    self._gogdl_bin, 'info', game_id, '--os', 'windows',
                    env={**env},
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, _err = await asyncio.wait_for(
                        proc.communicate(), timeout=_LANG_PROBE_TIMEOUT_S,
                    )
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.terminate()
                    return None
                return stdout if proc.returncode == 0 else None
        except Exception as e:
            logger.debug('[GOGDlc] lang probe: %s', e)
            return None

    @staticmethod
    def _parse_languages_from_info(stdout: bytes) -> list[str]:
        """Parse languages from info."""
        try:
            text = stdout.decode('utf-8', errors='replace')
        except Exception:
            return []
        for line in text.splitlines():
            if not line.strip().startswith('{'):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            langs = data.get('languages') if isinstance(data, dict) else None
            if isinstance(langs, list):
                return [str(l) for l in langs]
        return []

    async def install_dlc(
        self,
        game_id: str,
        dlc_id: str,
        base_path: str | None = None,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> Result:
        """Install DLC."""
        preflight = await self._dlc_preflight()
        if preflight is not None:
            return preflight
        path = self._dlc_resolve_base_path(game_id, base_path)
        lang = self._locale_fn() or 'en-US'
        proc = await self._dlc_spawn_gogdl(dlc_id, path, lang)
        if proc is None:
            return Result(success=False, error='gogdl_spawn_failed')
        await self._dlc_read_loop(proc, dlc_id, progress_cb)
        return await self._dlc_finalize(proc, dlc_id)

    async def _dlc_preflight(self) -> Result | None:
        """DLC preflight."""
        if not self._gogdl_bin:
            return Result(success=False, error='gogdl_not_found')
        if not await self._tokens.refresh_if_stale():
            return Result(success=False, error='no_tokens')
        return None

    def _dlc_resolve_base_path(
        self, game_id: str, base_path: str | None,
    ) -> str:
        """DLC resolve base path."""
        if base_path:
            return base_path
        info = self._resolve_install_path(game_id) or {}
        path = info.get('install_path') if isinstance(info, dict) else None
        return path or ''

    async def _dlc_spawn_gogdl(
        self, dlc_id: str, base_path: str, lang: str,
    ) -> asyncio.subprocess.Process | None:
        """DLC spawn GOGDL."""
        if not base_path:
            return None
        try:
            async with self._tokens.gogdl_credentials() as env:
                return await asyncio.create_subprocess_exec(
                    self._gogdl_bin, 'download',
                    dlc_id, '--platform', 'windows',
                    '--path', base_path, '--lang', lang,
                    env={**env},
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
        except OSError as e:
            logger.warning('[GOGDlc] spawn failed: %s', e)
            return None

    async def _dlc_read_loop(
        self,
        proc: asyncio.subprocess.Process,
        dlc_id: str,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """DLC read loop."""
        if proc.stdout is None:
            return
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode('utf-8', errors='replace').strip()
            await self._forward_dlc_progress(text, dlc_id, progress_cb)

    async def _dlc_finalize(
        self, proc: asyncio.subprocess.Process, dlc_id: str,
    ) -> Result:
        """DLC finalize."""
        rc = await proc.wait()
        if rc != 0:
            return Result(success=False, error=f'gogdl_rc:{rc}')
        return Result(success=True, data={'dlc_id': dlc_id})

    @staticmethod
    async def _forward_dlc_progress(
        line_str: str,
        dlc_id: str,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Forward DLC progress."""
        if progress_cb is None or not line_str:
            return
        await progress_cb({'phase': 'dlc', 'dlc_id': dlc_id, 'line': line_str})

    async def get_game_store_url(self, game_id: str) -> str | None:
        """Get game store URL."""
        if not self._config.api_gog_url:
            return None
        url = f'{self._config.api_gog_url}/products/{game_id}'
        data = await self._http_get_json(url, bearer=None)
        if isinstance(data, dict):
            slug = data.get('slug')
            if isinstance(slug, str) and slug:
                return f'https://www.gog.com/game/{slug}'
        return None

    async def _http_get_json(
        self, url: str, bearer: str | None,
    ) -> Any | None:
        """HTTP get JSON."""
        return await fetch_json_get(
            url,
            bearer=bearer,
            user_agent=self._config.user_agent,
            log_prefix='[GOGDlc]',
        )


