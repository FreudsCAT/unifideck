"""services/download/persistence.py — Queue JSON load/save.

Pure async helpers. Queue persisted as a top-level list of
``DownloadItem`` dicts (via ``DownloadItem.to_dict``). Errors
on load/save are logged + swallowed — a corrupted queue must
not crash the worker; service degrades gracefully to empty.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

from .models import DownloadItem

logger = logging.getLogger(__name__)


async def load_queue(queue_file: str) -> list[DownloadItem]:
    """Load the persisted queue from disk.

    Returns ``[]`` on: missing file, malformed JSON, top-level
    shape not a list, or per-item parse failure. Parse failures
    log at WARNING so ops sees corruption. Callers never receive
    partial data — all-or-nothing load keeps the worker sane.
    """
    if not await asyncio.to_thread(lambda: Path(queue_file).is_file()):
        return []

    def _read_sync() -> list[DownloadItem]:
        try:
            with Path(queue_file).open(encoding="utf-8") as f:
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
                    # TypeError is the conventional exception for a
                    # failed isinstance check; the broader try/except
                    # below catches it the same way ValueError would.
                    raise TypeError("Queue item is not a dictionary")
                items.append(DownloadItem.from_dict(item_dict))

            return items
        except json.JSONDecodeError as e:
            logger.warning("[DownloadPersistence] malformed JSON in queue file: %s", e)
            return []
        except Exception as e:
            logger.warning("[DownloadPersistence] failed to parse queue file: %s", e)
            return []

    return await asyncio.to_thread(_read_sync)


def _atomic_json_write(target: str, data: object) -> None:
    """Write ``data`` as JSON to ``target`` via tmp + atomic rename.

    Single-purpose helper introduced 2026-05-15 to keep
    ``save_queue``'s fan-out under the project's 10-callee
    policy. The write sequence (mkdir → open → write → flush →
    fsync → replace) collapses from 6 distinct callees into
    one in the caller.

    Errors propagate to the caller (typically logged at WARN
    and dropped — disk state recovers on next successful write).
    """
    parent = str(Path(target).parent)
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    tmp_path = target + ".tmp"
    try:
        with Path(tmp_path).open("w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_path).replace(target)
    except Exception:
        # Best-effort cleanup of the tmp file; reraise so the
        # caller sees the original failure.
        if Path(tmp_path).exists():
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
        raise


async def save_queue(
    queue_file: str, queue: list[DownloadItem],
) -> None:
    """Persist the queue to disk atomically (tmp + rename).

    Errors logged at WARNING, not raised — the in-memory queue
    remains the source of truth; next successful write recovers
    disk state.

    Refactor history (2026-05-15): extracted ``_atomic_json_write``
    helper to keep this function's fan-out under cap after the
    PTH migration introduced Path() as a new call target.
    """
    def _write_sync() -> None:
        try:
            data = [item.to_dict() for item in queue]
            _atomic_json_write(queue_file, data)
        except Exception as e:
            logger.warning(
                "[DownloadPersistence] failed to save queue: %s", e,
            )

    await asyncio.to_thread(_write_sync)
