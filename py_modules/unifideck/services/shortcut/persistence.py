"""services/shortcut/persistence.py — Atomic I/O for shortcuts.vdf + games.map.

Pure async helpers extracted from ``ShortcutService`` so the
orchestrator stays focused on the public API while I/O mechanics
(retry-on-corruption, tmpfile+os.replace) stay independently
testable.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any

import vdf

from .games_map import GameMapEntry, format_games_map, parse_games_map

logger = logging.getLogger(__name__)

# Games.map read retries — 3 x 100ms worst-case. Cheap enough to
# avoid spurious GameNotFoundError when the launcher reads
# mid-write by a concurrent background sync.
_GAMES_MAP_READ_ATTEMPTS = 3
_GAMES_MAP_RETRY_DELAY_S = 0.1


async def read_vdf(shortcuts_path: str) -> dict[str, Any]:
    """Load shortcuts.vdf into a dict (empty dict if missing).

    Offloaded via ``to_thread`` since the vdf library is sync.
    """
    if not await asyncio.to_thread(lambda: Path(shortcuts_path).is_file()):
        return {"shortcuts": {}}

    def _read_sync() -> dict[str, Any]:
        try:
            with Path(shortcuts_path).open("rb") as f:
                return vdf.binary_loads(f.read())
        except Exception as e:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
            logger.warning("[ShortcutPersistence] failed to read shortcuts.vdf: %s", e)
            return {"shortcuts": {}}

    return await asyncio.to_thread(_read_sync)


async def write_vdf(shortcuts_path: str, data: dict[str, Any]) -> None:
    """Persist shortcuts.vdf atomically.

    Uses tmpfile + os.replace pattern to prevent corruption on crash.
    """
    def _write_sync() -> None:
        parent = str(Path(shortcuts_path).parent)
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)

        tmp_path = shortcuts_path + ".tmp"
        try:
            with Path(tmp_path).open("wb") as f:
                f.write(vdf.binary_dumps(data))
            Path(tmp_path).replace(shortcuts_path)
        except Exception:
            logger.exception("[ShortcutPersistence] failed to write shortcuts.vdf")
            if Path(tmp_path).exists():
                with contextlib.suppress(OSError):
                    Path(tmp_path).unlink()

    await asyncio.to_thread(_write_sync)


async def read_games_map(games_map_path: str) -> dict[str, GameMapEntry]:
    """Load games.map with retry-on-corruption.

    Up to ``_GAMES_MAP_READ_ATTEMPTS`` attempts spaced
    ``_GAMES_MAP_RETRY_DELAY_S`` apart — a concurrent
    ``save_all`` can leave the file briefly partial between
    the truncate and the final flush. Transient errors
    (OSError rename race, UnicodeDecodeError mid-write)
    all retry. Returns ``{}`` on missing file or
    irrecoverable malformation.
    """
    if not await asyncio.to_thread(lambda: Path(games_map_path).is_file()):
        return {}

    for attempt in range(1, _GAMES_MAP_READ_ATTEMPTS + 1):
        try:
            def _read_sync() -> str:
                with Path(games_map_path).open(encoding="utf-8") as f:
                    return f.read()

            content = await asyncio.to_thread(_read_sync)
            return parse_games_map(content)
        except Exception as e:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
            if attempt < _GAMES_MAP_READ_ATTEMPTS:
                logger.debug(
                    "[ShortcutPersistence] games.map read failed (attempt %d/%d): %s. Retrying...",
                    attempt, _GAMES_MAP_READ_ATTEMPTS, e,
                )
                await asyncio.sleep(_GAMES_MAP_RETRY_DELAY_S)
            else:
                logger.warning(
                    "[ShortcutPersistence] games.map read failed permanently after %d attempts: %s",
                    _GAMES_MAP_READ_ATTEMPTS, e,
                )

    return {}


async def write_games_map(games_map_path: str, games_map: dict[str, GameMapEntry]) -> None:
    """Persist games.map atomically.

    Uses the POSIX ``tmpfile + os.replace`` pattern: write content to
    ``<path>.tmp``, then rename. Readers mid-read see either
    old or new content, never a half-written file — eliminates
    the race where the launcher dispatcher reads between our
    truncate and the subsequent writes.
    """
    def _write_sync() -> None:
        parent = str(Path(games_map_path).parent)
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)

        content = format_games_map(games_map)
        tmp_path = games_map_path + ".tmp"

        try:
            with Path(tmp_path).open("w", encoding="utf-8") as f:
                f.write(content)
                # Ensure it's fully written to disk before rename
                f.flush()
                os.fsync(f.fileno())

            Path(tmp_path).replace(games_map_path)
        except Exception:
            logger.exception("[ShortcutPersistence] failed to write games.map")
            if Path(tmp_path).exists():
                with contextlib.suppress(OSError):
                    Path(tmp_path).unlink()

    await asyncio.to_thread(_write_sync)
