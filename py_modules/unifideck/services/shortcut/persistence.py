"""Shortcut persistence — load/save the games map + VDF index.

OP-14g | py_modules/unifideck/services/shortcut/persistence.py

Module-level functions (no class) for the persistence layer of the
shortcut service :

* ``load_shortcuts`` — read the shortcuts.vdf via the VDF lib;
* ``load_games_map`` — read the JSON game map from disk;
* ``save_all`` — atomic snapshot of both files in one call.

Kept module-level because they're stateless and shared between the
mixin and the constructor.
"""

from __future__ import annotations
import asyncio
import logging
import os as _os
from typing import Any
from ...core.io import async_file_ops as aio
from ...steam.shortcuts import (
    read_shortcuts as _vdf_read,
    write_shortcuts as _vdf_write,
)
from .games_map import GameMapEntry, format_games_map, parse_games_map

logger = logging.getLogger(__name__)
_GAMES_MAP_READ_ATTEMPTS = 3
_GAMES_MAP_RETRY_DELAY_S = 0.1


async def load_shortcuts(shortcuts_path: str) -> list[dict[str, Any]]:
    """Load shortcuts."""
    if not await aio.is_file(shortcuts_path):
        return []
    return await asyncio.to_thread(_vdf_read, shortcuts_path)


async def load_games_map(games_map_path: str) -> dict[str, GameMapEntry]:
    """Load games map."""
    if not await aio.is_file(games_map_path):
        return {}
    last_err: Exception | None = None
    for attempt in range(_GAMES_MAP_READ_ATTEMPTS):
        try:
            content = await aio.read_text(games_map_path)
            if content is None:
                last_err = OSError("read_text returned None")
                continue
            return parse_games_map(content)
        except Exception as err:
            last_err = err
            logger.warning(
                "[ShortcutService] games.map read attempt %d/%d failed: %s",
                attempt + 1,
                _GAMES_MAP_READ_ATTEMPTS,
                err,
            )
            await asyncio.sleep(_GAMES_MAP_RETRY_DELAY_S)
    logger.error(
        "[ShortcutService] games.map read gave up after %d attempts "
        "(last error: %s), starting empty",
        _GAMES_MAP_READ_ATTEMPTS,
        last_err,
    )
    return {}


async def save_all(
    shortcuts_path: str,
    shortcuts: list[dict[str, Any]],
    games_map_path: str,
    games_map: dict[str, GameMapEntry],
) -> None:
    """Save all."""
    await asyncio.to_thread(_vdf_write, shortcuts_path, shortcuts)
    content = format_games_map(games_map)
    tmp_path = f"{games_map_path}.tmp"
    await aio.write_text(tmp_path, content)
    await asyncio.to_thread(_os.replace, tmp_path, games_map_path)
