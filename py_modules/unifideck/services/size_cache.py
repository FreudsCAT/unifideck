"""Persistent download-size cache.

py_modules/unifideck/services/size_cache.py

Maps ``"<store>:<game_id>"`` → download size in bytes for
*not-installed* games so the App-Details "Space Required" row is
instant after the first lookup — including across plugin restarts and
reinstalls (the file lives under the data dir, not the plugin dir).

Installed games are deliberately NOT cached here: their size is a fast
on-disk directory walk that changes over time (saves, updates, DLC), so
it's always recomputed live.

Best-effort throughout: read/write failures are logged and swallowed; a
missing or corrupt cache just degrades to a live store lookup.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# One cache instance per file path, shared process-wide so concurrent
# callers (play-section + info-panel) hit the same in-memory dict.
_INSTANCES: dict[str, SizeCache] = {}


def get_size_cache(path: str) -> SizeCache:
    """Return the process-wide :class:`SizeCache` for ``path``."""
    inst = _INSTANCES.get(path)
    if inst is None:
        inst = SizeCache(path)
        _INSTANCES[path] = inst
    return inst


class SizeCache:
    """Lazily-loaded, write-through ``{"store:game_id": bytes}`` cache."""

    def __init__(self, path: str) -> None:
        """Store the on-disk path; defer the load until first access."""
        self._path = path
        self._data: dict[str, int] | None = None
        self._lock = asyncio.Lock()

    async def get(self, store: str, game_id: str) -> int | None:
        """Cached size in bytes, or ``None`` on miss / non-positive value."""
        async with self._lock:
            await self._ensure_loaded()
            assert self._data is not None
            value = self._data.get(f"{store}:{game_id}")
        return value if isinstance(value, int) and value > 0 else None

    async def put(self, store: str, game_id: str, size_bytes: int) -> None:
        """Record ``size_bytes`` and flush to disk atomically."""
        if size_bytes <= 0:
            return
        async with self._lock:
            await self._ensure_loaded()
            assert self._data is not None
            self._data[f"{store}:{game_id}"] = int(size_bytes)
            snapshot = dict(self._data)
        await asyncio.to_thread(self._write, snapshot)

    async def _ensure_loaded(self) -> None:
        if self._data is None:
            self._data = await asyncio.to_thread(self._read)

    def _read(self) -> dict[str, int]:
        try:
            p = Path(self._path)
            if p.is_file():
                with p.open(encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return {
                        str(k): int(v)
                        for k, v in data.items()
                        if isinstance(v, (int, float)) and v > 0
                    }
        except Exception as e:
            logger.warning("[SizeCache] read failed (%s): %s", self._path, e)
        return {}

    def _write(self, data: dict[str, int]) -> None:
        tmp = f"{self._path}.tmp"
        try:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            with Path(tmp).open("w", encoding="utf-8") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            Path(tmp).replace(self._path)
        except Exception as e:
            logger.warning("[SizeCache] write failed (%s): %s", self._path, e)
            with contextlib.suppress(OSError):
                Path(tmp).unlink()
