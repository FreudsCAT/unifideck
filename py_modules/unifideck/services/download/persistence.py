"""services/download/persistence.py — Queue JSON load/save.

Pure async helpers. Queue persisted as a top-level list of
``DownloadItem`` dicts (via ``DownloadItem.to_dict``). Errors
on load/save are logged + swallowed — a corrupted queue must
not crash the worker; service degrades gracefully to empty.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from .models import DownloadItem

logger = logging.getLogger(__name__)


async def load_queue(queue_file: str) -> list[DownloadItem]:
    """Load the persisted queue from disk.

    Returns ``[]`` on: missing file, malformed JSON, top-level
    shape not a list, or per-item parse failure. Parse failures
    log at WARNING so ops sees corruption. Callers never receive
    partial data — all-or-nothing load keeps the worker sane.
    """
    if not os.path.isfile(queue_file):
        return []

    def _read_sync() -> list[DownloadItem]:
        try:
            with open(queue_file, encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, list):
                logger.warning(
                    "[DownloadPersistence] queue file %s is not a list, starting empty",
                    queue_file,
                )
                return []

            items = []
            for item_dict in data:
                if not isinstance(item_dict, dict):
                    raise ValueError("Queue item is not a dictionary")
                items.append(DownloadItem.from_dict(item_dict))

            return items
        except json.JSONDecodeError as e:
            logger.warning("[DownloadPersistence] malformed JSON in queue file: %s", e)
            return []
        except Exception as e:
            logger.warning("[DownloadPersistence] failed to parse queue file: %s", e)
            return []

    return await asyncio.to_thread(_read_sync)


async def save_queue(
    queue_file: str, queue: list[DownloadItem],
) -> None:
    """Persist the queue to disk atomically (tmp + rename).

    Errors logged at WARNING, not raised — the in-memory queue
    remains the source of truth; next successful write recovers
    disk state.
    """
    def _write_sync() -> None:
        try:
            parent = os.path.dirname(queue_file)
            if parent:
                os.makedirs(parent, exist_ok=True)

            data = [item.to_dict() for item in queue]
            tmp_path = queue_file + ".tmp"

            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, queue_file)
        except Exception as e:
            logger.warning("[DownloadPersistence] failed to save queue: %s", e)
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    await asyncio.to_thread(_write_sync)
