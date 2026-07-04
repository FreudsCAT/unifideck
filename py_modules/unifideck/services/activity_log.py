"""ActivityLogService — rotating JSONL log of recent library syncs.

Subscribes to ``LIBRARY_SYNC_STARTED`` / ``LIBRARY_SYNC_COMPLETED`` /
``LIBRARY_SYNC_CANCELLED`` and appends a structured record to
``runtime_dir/sync_activity.log``. Rotates at 100 entries so the
file stays small enough to load synchronously when the UI asks for
the "recent syncs" panel.

Why JSONL not SQLite
====================
Activity events are append-only, read-rarely, and the row count is
bounded. SQLite would add startup overhead (open + WAL replay) and a
schema migration story for what's essentially a circular buffer of
~100 lines.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

MAX_ENTRIES = 100


class ActivityLogService:
    """Persist sync lifecycle events to a rotating JSONL file."""

    def __init__(self, bus: EventBus, log_path: str) -> None:
        """Store path + bus, auto-wire the subscribers.

        ``log_path`` should live in the runtime / data directory
        (see ``ServicePaths.activity_log``). Parent dir is created
        lazily on the first write.
        """
        self._bus = bus
        self._path = Path(log_path)
        # Single mutex per service so two concurrent writes can't
        # interleave inside the read-modify-write rotation.
        self._lock = asyncio.Lock()
        auto_wire(self, self._bus)

    async def stop(self) -> None:
        """Lifecycle hook — currently a no-op."""

    @subscribe(Events.LIBRARY_SYNC_STARTED)
    async def _on_started(self, **kwargs: Any) -> None:
        await self._append({"event": "started", **kwargs})

    @subscribe(Events.LIBRARY_SYNC_COMPLETED)
    async def _on_completed(self, **kwargs: Any) -> None:
        await self._append({"event": "completed", **kwargs})

    @subscribe(Events.LIBRARY_SYNC_CANCELLED)
    async def _on_cancelled(self, **kwargs: Any) -> None:
        await self._append({"event": "cancelled", **kwargs})

    async def _append(self, record: dict[str, Any]) -> None:
        """Tack a record onto the JSONL log, rotating if needed.

        Adds a ``ts_ms`` field so consumers always have a reliable
        timestamp without relying on the file's mtime.
        """
        record.setdefault("ts_ms", int(time.time() * 1000))
        async with self._lock:
            try:
                await asyncio.to_thread(self._write_record, record)
            except OSError:
                logger.exception(
                    "[ActivityLog] failed to write %s", self._path,
                )

    def _write_record(self, record: dict[str, Any]) -> None:
        """Synchronous write — called via ``asyncio.to_thread``.

        Reads the existing lines, drops oldest if over the cap,
        rewrites atomically via ``temp + replace`` to avoid leaving
        the file truncated on crash.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._read_existing()
        existing.append(record)
        if len(existing) > MAX_ENTRIES:
            existing = existing[-MAX_ENTRIES:]
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for entry in existing:
                fh.write(json.dumps(entry, ensure_ascii=False))
                fh.write("\n")
        tmp.replace(self._path)

    def _read_existing(self) -> list[dict[str, Any]]:
        """Load prior records from disk. Returns ``[]`` on missing / corrupt."""
        if not self._path.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            for raw_line in self._path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError as e:
            logger.warning("[ActivityLog] read failed: %s", e)
            return []
        return out

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most-recent N records (newest first).

        Exposed for RPC handlers wanting to render a "last 10 syncs"
        panel. Reads from disk on every call — there's no in-memory
        cache because the typical access pattern is "open settings
        once" rather than polling.
        """
        async with self._lock:
            entries = await asyncio.to_thread(self._read_existing)
        return list(reversed(entries[-max(1, limit):]))
