"""Queue persistence — save/restore across plugin reboots.

OP-15e | py_modules/unifideck/services/download/persistence.py

``load_queue`` / ``save_queue`` are the two functions that serialise
the download queue to a JSON file. Used on boot to rehydrate the
queue and on every state change to flush the latest snapshot.

Atomic write via temp + ``os.replace`` to avoid corruption.
"""

from __future__ import annotations
import logging
from typing import Any, cast
from ...core.io import async_file_ops as aio
from .models import DownloadItem

logger = logging.getLogger(__name__)


async def load_queue(queue_file: str) -> list[DownloadItem]:
    """Load queue."""
    if not await aio.is_file(queue_file):
        return []
    try:
        raw_data = await aio.read_json(queue_file)
        data: list[dict[str, Any]] = (
            cast("list[dict[str, Any]]", raw_data) if isinstance(raw_data, list) else []
        )
    except Exception as e:
        logger.warning(
            "[DownloadService] queue load failed: %s",
            e,
        )
        return []
    return [DownloadItem.from_dict(raw) for raw in data]


async def save_queue(queue_file: str, queue: list[DownloadItem]) -> None:
    """Save queue."""
    data = [i.to_dict() for i in queue]
    try:
        await aio.write_json(queue_file, data)
    except Exception as e:
        logger.warning(
            "[DownloadService] queue save failed: %s",
            e,
        )
