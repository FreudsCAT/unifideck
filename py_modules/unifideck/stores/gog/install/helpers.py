"""helpers.py — Language probe + picker for the install pipeline.

# OP-51h | py_modules/unifideck/stores/gog/install/helpers.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from .languages import smart_match_language

if TYPE_CHECKING:
    from .installer import GOGInstaller

logger = logging.getLogger(__name__)
_LANG_PROBE_TIMEOUT_S = 30.0


class _InstallHelpers:
    """Install helpers."""

    def __init__(self, parent: GOGInstaller) -> None:
        """Initialize the instance."""
        self._parent = parent

    async def probe_game_info(
        self, game_id: str,
    ) -> tuple[str, str | None, list[str]]:
        """Probe game info."""
        gogdl_bin = self._parent._gogdl_bin
        if not gogdl_bin:
            return 'windows', None, []
        try:
            async with self._parent._tokens.gogdl_credentials() as env:
                proc = await asyncio.create_subprocess_exec(
                    gogdl_bin, 'info', game_id, '--os', 'windows',
                    env={**env},
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, _err = await asyncio.wait_for(
                        proc.communicate(), timeout=_LANG_PROBE_TIMEOUT_S,
                    )
                except TimeoutError:
                    return 'windows', None, []
                if proc.returncode != 0:
                    return 'windows', None, []
        except OSError as e:
            logger.debug('[GOGHelpers] probe failed: %s', e)
            return 'windows', None, []
        folder, langs = self.parse_info_output(
            stdout.decode('utf-8', errors='replace'),
        )
        return 'windows', folder, langs

    @staticmethod
    def parse_info_output(
        stdout: str,
    ) -> tuple[str | None, list[str]]:
        """Parse info output."""
        folder: str | None = None
        languages: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if folder is None and isinstance(data.get('folder'), str):
                folder = data['folder']
            langs = data.get('languages')
            if isinstance(langs, list) and not languages:
                languages = [str(l) for l in langs]
        return folder, languages

    @staticmethod
    def pick_languages(
        primary_lang: str, explicit: bool, supported: list[str],
    ) -> list[str]:
        """Pick languages."""
        if not supported:
            return ['en-US']
        if explicit:
            return _InstallHelpers._pick_explicit_lang(primary_lang, supported)
        return _InstallHelpers._pick_implicit_langs(primary_lang, supported)

    @staticmethod
    def _pick_explicit_lang(
        primary_lang: str, supported: list[str],
    ) -> list[str]:
        """Pick explicit lang."""
        match = smart_match_language(primary_lang, supported)
        if match:
            return [match]
        return ['en-US' if 'en-US' in supported else supported[0]]

    @staticmethod
    def _pick_implicit_langs(
        primary_lang: str, supported: list[str],
    ) -> list[str]:
        """Pick implicit langs."""
        out: list[str] = []
        match = smart_match_language(primary_lang, supported)
        if match:
            out.append(match)
        for fallback in ('en-US', 'en'):
            picked = smart_match_language(fallback, supported)
            if picked and picked not in out:
                out.append(picked)
        return out or supported[:1]
