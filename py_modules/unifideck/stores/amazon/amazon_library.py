"""amazon_library.py — Read owned + installed Amazon games from nile's caches.

# OP-49c | py_modules/unifideck/stores/amazon/amazon_library.py | Depends: OP-49a

Nile stores owned-library and installed-game state under its config
dir (``~/.config/nile``). We parse those JSON blobs directly instead
of shelling out to ``nile`` because the CLI's output isn't stable.
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
        self._config_dir = config_dir

    async def read_owned_games(self) -> list[Game]:
        """Read owned games."""
        path = str(Path(self._config_dir).expanduser() / 'library.json')
        data = await self._read_json(path)
        if not isinstance(data, list):
            return []
        games: list[Game] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            product = entry.get('product') or {}
            if not isinstance(product, dict):
                continue
            game_id = str(
                entry.get('id')
                or product.get('id')
                or product.get('asin')
                or '',
            )
            if not game_id:
                continue
            title = str(
                product.get('title')
                or entry.get('title')
                or game_id,
            )
            games.append(
                Game(
                    store='amazon',
                    game_id=game_id,
                    title=title,
                    installed=False,
                    install_path='',
                ),
            )
        logger.info('[amazon_library] %d owned games', len(games))
        return games

    async def read_installed_ids(self) -> dict[str, dict[str, Any]]:
        """Read installed ids."""
        path = str(Path(self._config_dir).expanduser() / 'installed.json')
        data = await self._read_json(path)
        if not isinstance(data, list):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            game_id = str(entry.get('id') or '')
            if not game_id:
                continue
            out[game_id] = entry
        return out

    async def get_official_url(self, game_id: str) -> str | None:
        """Get official URL."""
        if not game_id:
            return None
        path = str(Path(self._config_dir).expanduser() / 'library.json')
        data = await self._read_json(path)
        if not isinstance(data, list):
            return None
        for entry in data:
            if not isinstance(entry, dict):
                continue
            entry_id = str(
                entry.get('id') or (entry.get('product') or {}).get('id') or '',
            )
            if entry_id != game_id:
                continue
            product = entry.get('product') or {}
            slug = product.get('productDetail', {}).get(
                'product', {},
            ).get('vendorSku') if isinstance(product, dict) else None
            asin = product.get('asin') if isinstance(product, dict) else None
            if isinstance(asin, str) and asin:
                return f'https://www.amazon.com/gp/product/{asin}'
            if isinstance(slug, str) and slug:
                return f'https://gaming.amazon.com/{slug}'
        return None

    async def _read_json(self, path: str) -> Any:
        """Read JSON."""
        if not await aio.is_file(path):
            return None
        try:
            text = await aio.read_text(path)
        except Exception as e:
            logger.debug('[amazon_library] read %s: %s', path, e)
            return None
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning('[amazon_library] json decode %s: %s', path, e)
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
            game.install_path = str(info.get('path') or info.get('install_path') or '')
        out.append(game)
    return out
