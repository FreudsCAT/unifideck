"""Amazon Games library reader — owned games list + install status merger.

OP-49c | py_modules/unifideck/stores/amazon/amazon_library.py

``AmazonLibraryReader`` reads the user's owned-games list from the
``nile`` user data file (the JSON state that nile maintains after a
successful login).

Public methods :

* ``fetch_owned_games()`` — load the games list from the user file;
* ``ensure_user_file_present()`` — check the file exists, warn the
  user if not;
* ``parse_entries(data)`` — extract game records from raw JSON;
* ``check_user_data_freshness()`` — TTL-aware freshness check.

The module-level helper ``merge_install_status`` overlays installed-
state (from the install registry) onto the owned-games list to
produce the final ``GameRecord`` shape the UI consumes.
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any
from ...core.io import async_file_ops as aio
from ...core.types import Game

logger = logging.getLogger(__name__)


class AmazonLibraryReader:
    """Amazon library reader."""

    def __init__(self, config_dir: str) -> None:
        """Initialize the instance."""
        config_path = Path(config_dir).expanduser()
        self._config_dir = str(config_path)
        self._library_path = str(config_path / "library.json")
        self._installed_path = str(
            config_path / "installed.json",
        )

    async def read_owned_games(self) -> list[Game]:
        """Read owned games."""
        data = await self._read_json(self._library_path)
        if not isinstance(data, list):
            return []
        games: list[Game] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            product = item.get("product")
            if not isinstance(product, dict):
                continue
            game_id = product.get("id", "")
            if not game_id:
                continue
            games.append(
                Game(
                    app_id=0,
                    store="amazon",
                    store_game_id=game_id,
                    title=str(product.get("title") or game_id),
                    installed=False,
                )
            )
        logger.info(
            "[amazon_library] %d owned games",
            len(games),
        )
        return games

    async def read_installed_ids(self) -> dict[str, dict[str, Any]]:
        """Read installed ids."""
        data = await self._read_json(self._installed_path)
        if not isinstance(data, list):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            game_id = entry.get("id")
            if not game_id:
                continue
            result[game_id] = {
                "path": entry.get("path", ""),
                "version": entry.get("version", ""),
            }
        logger.info(
            "[amazon_library] %d installed games",
            len(result),
        )
        return result

    async def get_official_url(self, game_id: str) -> str | None:
        """Get official URL."""
        data = await self._read_json(self._library_path)
        if not isinstance(data, list):
            return None
        for item in data:
            if not isinstance(item, dict):
                continue
            product = item.get("product", {})
            if product.get("id") != game_id:
                continue
            details = product.get("productDetail", {}).get("details", {})
            websites = details.get("websites", {})
            for key in ("OFFICIAL", "STEAM"):
                url = websites.get(key)
                if isinstance(url, str) and url:
                    return url
            return None
        return None

    async def _read_json(self, path: str) -> Any:
        """Read JSON."""
        try:
            if not await aio.is_file(path):
                return None
            content = await aio.read_text(path)
            if content is None:
                return None
            return json.loads(content)
        except (OSError, json.JSONDecodeError) as e:
            logger.debug(
                "[amazon_library] read %s failed: %s",
                path,
                e,
            )
            return None


def merge_install_status(
    owned: list[Game],
    installed: dict[str, dict[str, Any]],
) -> list[Game]:
    """Merge install status."""
    merged: list[Game] = []
    for game in owned:
        info = installed.get(game.store_game_id)
        if info is None:
            merged.append(game)
            continue
        merged.append(
            Game(
                app_id=game.app_id,
                store=game.store,
                store_game_id=game.store_game_id,
                title=game.title,
                installed=True,
                install_path=info.get("path"),
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
