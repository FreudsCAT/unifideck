"""services/download/worker_helpers.py — module-level worker utilities.

Extracted from ``worker.py`` to keep that file under the 550-LOC
volumetry cap. Pure module-level helpers — no mixin/host state.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .models import DownloadItem

# Strong references to background install tasks so the GC can't
# collect them mid-flight (see RUF006).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def track_task(task: asyncio.Task[Any]) -> None:
    """Register a fire-and-forget task so the GC doesn't collect it early."""
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# (progress-key, DownloadItem attribute, converter) for the structured
# progress payloads emitted by GOG / Ubisoft. Driven by a table so
# ``apply_dict_progress`` stays flat (no per-field if-cascade).
_PROGRESS_FIELDS: tuple[tuple[str, str, Any], ...] = (
    ("downloaded_bytes", "downloaded_bytes", int),
    ("total_bytes", "total_bytes", int),
    ("eta_seconds", "eta_seconds", int),
    ("phase", "download_phase", str),
    ("phase_message", "phase_message", str),
)


def apply_dict_progress(item: DownloadItem, progress: dict[str, Any]) -> None:
    """Copy a structured progress payload (GOG/Ubisoft) onto *item*."""
    pct = progress.get("percentage") or progress.get("progress_percent")
    if isinstance(pct, (int, float)):
        item.progress = float(pct)
    if "speed_mbps" in progress:
        item.speed_mbps = float(progress["speed_mbps"])
    elif "speed_bps" in progress:
        item.speed_mbps = float(progress["speed_bps"]) / (1024 * 1024)
    for pkey, attr, conv in _PROGRESS_FIELDS:
        if pkey in progress:
            setattr(item, attr, conv(progress[pkey]))
