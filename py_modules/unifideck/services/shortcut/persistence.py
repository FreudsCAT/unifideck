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
    """Read ``shortcuts.vdf`` and return the entries as a flat list.

    Delegates to ``steam.shortcuts.read_shortcuts`` (which parses
    the binary VDF format) inside ``asyncio.to_thread`` so the
    blocking parse doesn't stall the event loop on large files.

    Args:
        shortcuts_path: absolute path to Steam's ``shortcuts.vdf``.

    Returns:
        List of shortcut dicts. Empty list when the file doesn't
        exist (fresh Steam install, brand-new user) — the caller
        treats that as "no shortcuts yet".
    """
    if not await aio.is_file(shortcuts_path):
        return []
    return await asyncio.to_thread(_vdf_read, shortcuts_path)


async def load_games_map(games_map_path: str) -> dict[str, GameMapEntry]:
    """Read the games-map file with retry-on-transient-error semantics.

    The games-map file is written atomically (temp + rename) but
    the in-place read can occasionally race with the rename if
    another part of the plugin saves at the wrong moment. To
    accommodate that, we retry up to 3 times with a 100 ms delay
    between attempts.

    After all retries fail, returns an empty dict and logs at
    ERROR — a missing games map is recoverable (reconcile will
    rebuild it from the live game list on the next sync), so a
    hard failure here would be more harmful than the missed read.

    Args:
        games_map_path: absolute path to the games-map file.

    Returns:
        Mapping ``"<store>:<game_id>" → GameMapEntry``. Empty
        dict on missing or unreadable file.
    """
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
    """Persist both ``shortcuts.vdf`` and the games-map file.

    Both writes are atomic:

    * ``shortcuts.vdf`` — handled internally by
      ``steam.shortcuts.write_shortcuts`` (which uses its own
      temp + rename via the bundled vdf library);
    * games map — written here via temp + ``os.replace``.

    The two writes are **not** atomic relative to each other —
    a crash between them could leave the games map ahead of
    shortcuts.vdf or vice versa. In practice that's harmless:
    ``reconcile`` will repair the divergence on the next sync.

    Args:
        shortcuts_path: absolute path to ``shortcuts.vdf``.
        shortcuts: in-memory list of shortcut dicts.
        games_map_path: absolute path to the games-map file.
        games_map: in-memory mapping.
    """
    await asyncio.to_thread(_vdf_write, shortcuts_path, shortcuts)
    content = format_games_map(games_map)
    tmp_path = f"{games_map_path}.tmp"
    await aio.write_text(tmp_path, content)
    await asyncio.to_thread(_os.replace, tmp_path, games_map_path)
