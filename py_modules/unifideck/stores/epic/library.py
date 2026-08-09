"""Epic Games library reader — owned games + install status merger.

OP-48c | py_modules/unifideck/stores/epic/library.py

``EpicLibraryReader`` queries ``legendary`` for the user's owned
games list and merges it with locally-detected install state to
produce the ``GameRecord`` shape consumed by the UI.

Public methods :

* ``fetch_owned_games()`` — call ``legendary list`` and parse output;
* ``parse_legendary_entries(data)`` — extract game records;
* ``check_freshness()`` — TTL-aware staleness check on the cache;
* ``invalidate_cache()`` — force re-fetch on next call;
* ``has_legendary()`` — sanity check that the binary is available;
* ``refresh_now()`` — manual refresh.

Module-level helper ``merge_install_status`` overlays installed-state
from the install registry onto the owned-games list. Filtering of
"non-game" assets (UE marketplace stuff, mods, plugins) is delegated
to ``filter.py`` (OP-48f).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from unifideck.core.binaries import clean_cli_env
from unifideck.core.types import Game

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
        self._timeout = library_timeout
        self._installed_ttl = installed_ttl
        self._installed_cache: dict[str, dict[str, Any]] | None = None
        self._installed_cache_ts: float = 0.0

    async def read_owned_games(self) -> list[Game]:
        """Read owned games."""
        if not self._cli_path:
            return []
        data = await self._run_legendary_json(["list", "--json"])
        if not isinstance(data, list):
            return []
        games: list[Game] = []
        filtered = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            if should_filter_epic_item(item):
                filtered += 1
                continue
            app_name = item.get("app_name", "")
            if not app_name:
                continue
            games.append(
                Game(
                    app_id=0,
                    store="epic",
                    store_game_id=app_name,
                    title=item.get("app_title") or app_name,
                    installed=False,
                )
            )
        logger.info("[epic_library] %d owned games (%d filtered)", len(games), filtered)
        return games

    async def read_installed_map(self, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        """Read installed map."""
        if not self._cli_path:
            return {}
        if not force_refresh and self._installed_cache is not None:
            age = time.time() - self._installed_cache_ts
            if age < self._installed_ttl:
                return self._installed_cache
        result = await self._load_installed_from_cli()
        self._installed_cache = result
        self._installed_cache_ts = time.time()
        logger.info("[epic_library] %d installed games", len(result))
        return result

    async def _load_installed_from_cli(self) -> dict[str, dict[str, Any]]:
        """Load installed from cli."""
        data = await self._run_legendary_json(
            ["list-installed", "--json"],
        )
        result: dict[str, dict[str, Any]] = {}
        if not isinstance(data, list):
            return result
        for entry in data:
            if not isinstance(entry, dict):
                continue
            app_name = entry.get("app_name")
            if app_name:
                result[app_name] = entry
        return result

    def invalidate_installed_cache(self) -> None:
        """Invalidate installed cache."""
        self._installed_cache = None
        self._installed_cache_ts = 0.0

    async def _run_legendary_json(self, args: list[str]) -> Any:
        """Run LEGENDARY JSON."""
        if self._cli_path is None:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=clean_cli_env(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout,
            )
        except (TimeoutError, OSError) as e:
            logger.warning("[epic_library] legendary %s failed: %s", args[0], e)
            return None
        if proc.returncode != 0:
            logger.error(
                "[epic_library] legendary %s exit %d: %s",
                args[0],
                proc.returncode,
                stderr.decode(errors="ignore")[:200],
            )
            return None
        try:
            return json.loads(stdout.decode(errors="ignore"))
        except json.JSONDecodeError:
            logger.exception("[epic_library] JSON parse error")
            return None


def merge_install_status(
    owned: list[Game],
    installed: dict[str, dict[str, Any]],
) -> list[Game]:
    """Merge install status."""
    merged: list[Game] = []
    for game in owned:
        entry = installed.get(game.store_game_id)
        if entry is None:
            merged.append(game)
            continue
        # legendary's ``list-installed`` puts ``install_path`` at the top
        # level; accept a nested ``install`` dict too for older call sites.
        install_data = entry.get("install", {}) or {}
        install_path = entry.get("install_path") or install_data.get(
            "install_path",
        )
        # Verify the files are actually on disk. legendary's installed.json
        # can outlive the directory — e.g. "Delete all data" (or a manual
        # rm) removes the files but not legendary's record — and without
        # this the next sync re-marks the game installed, so Steam shows
        # PLAY for a game with no files. Treat a missing dir as not-installed.
        if install_path and not Path(install_path).is_dir():
            merged.append(game)
            continue
        merged.append(
            Game(
                app_id=game.app_id,
                store=game.store,
                store_game_id=game.store_game_id,
                title=game.title,
                installed=True,
                install_path=(install_path or None),
                exe_path=game.exe_path,
                icon_url=game.icon_url,
                hero_url=game.hero_url,
                logo_url=game.logo_url,
                size_bytes=game.size_bytes,
                tags=list(game.tags),
                metadata=dict(game.metadata),
            )
        )
    return merged
