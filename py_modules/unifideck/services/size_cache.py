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
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# How long a "the store could not tell us" answer is trusted before we try
# again. Without this, an unresolvable game pays the FULL live lookup on
# every single page open and still shows nothing — measured on a real
# device, 138 of 608 games were in that state, which is exactly the
# multi-second "Space Required" gap users report as never improving.
#
# Six hours: long enough that repeat opens are instant, short enough that a
# transient outage (or a repaired store login) heals the same day.
UNKNOWN_TTL_S = 6 * 3600

# One cache instance per file path, shared process-wide so concurrent
# callers (play-section + info-panel) hit the same in-memory dict.
_INSTANCES: dict[str, SizeCache] = {}


def _as_mapping(value: object) -> dict[str, object]:
    """Coerce a loaded JSON value to a dict — anything else reads as empty.

    The cache file is user-writable and can be hand-edited or truncated, so
    a non-mapping under ``sizes``/``unknown`` must degrade to "no entries"
    rather than raise on ``.items()``.
    """
    return value if isinstance(value, dict) else {}


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
        # "store:game_id" → unix ts of the last failed lookup. See
        # ``UNKNOWN_TTL_S``; persisted alongside the sizes so the reprieve
        # survives the restarts that happen right after a sync.
        self._unknown: dict[str, float] = {}
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
            # A real answer supersedes any earlier failure.
            self._unknown.pop(f"{store}:{game_id}", None)
            snapshot = dict(self._data)
            unknown = dict(self._unknown)
        await asyncio.to_thread(self._write, snapshot, unknown)

    async def is_unknown(self, store: str, game_id: str) -> bool:
        """True when a recent lookup already failed for this game.

        Lets callers skip a live store call that is very unlikely to answer,
        so the UI resolves immediately instead of stalling for seconds and
        then showing nothing anyway.
        """
        async with self._lock:
            await self._ensure_loaded()
            ts = self._unknown.get(f"{store}:{game_id}")
        return ts is not None and (time.time() - ts) < UNKNOWN_TTL_S

    async def mark_unknown(self, store: str, game_id: str) -> None:
        """Record that the store could not supply a size for this game."""
        async with self._lock:
            await self._ensure_loaded()
            assert self._data is not None
            self._unknown[f"{store}:{game_id}"] = time.time()
            snapshot = dict(self._data)
            unknown = dict(self._unknown)
        await asyncio.to_thread(self._write, snapshot, unknown)

    async def _ensure_loaded(self) -> None:
        if self._data is None:
            self._data, self._unknown = await asyncio.to_thread(self._read)

    def _read(self) -> tuple[dict[str, int], dict[str, float]]:
        """Load sizes + failure stamps, accepting the legacy flat format.

        Files written before negative caching are a bare
        ``{"store:id": bytes}`` map; they load as sizes with no failure
        stamps, so an existing warm cache is preserved verbatim.
        """
        try:
            p = Path(self._path)
            if p.is_file():
                with p.open(encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return self._parse(data)
        except Exception as e:
            logger.warning("[SizeCache] read failed (%s): %s", self._path, e)
        return {}, {}

    @staticmethod
    def _parse(
        data: dict[str, object],
    ) -> tuple[dict[str, int], dict[str, float]]:
        """Split a loaded payload into (sizes, failure stamps)."""
        raw_sizes = data.get("sizes") if "sizes" in data else data
        raw_unknown = data.get("unknown") if "unknown" in data else {}
        sizes = {
            str(k): int(v)
            for k, v in _as_mapping(raw_sizes).items()
            if isinstance(v, (int, float)) and v > 0
        }
        unknown = {
            str(k): float(v)
            for k, v in _as_mapping(raw_unknown).items()
            if isinstance(v, (int, float)) and v > 0
        }
        return sizes, unknown

    def _write(
        self, data: dict[str, int], unknown: dict[str, float] | None = None,
    ) -> None:
        tmp = f"{self._path}.tmp"
        payload = {"sizes": data, "unknown": unknown or {}}
        try:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            with Path(tmp).open("w", encoding="utf-8") as f:
                json.dump(payload, f)
                f.flush()
                os.fsync(f.fileno())
            Path(tmp).replace(self._path)
        except Exception as e:
            logger.warning("[SizeCache] write failed (%s): %s", self._path, e)
            with contextlib.suppress(OSError):
                Path(tmp).unlink()
