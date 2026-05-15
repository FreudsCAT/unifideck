"""services/download/validators.py — Path validation + queue key derivation.

Pure helpers — no service state, no I/O coupling. Kept
separate so the service layer stays focused on orchestration
while file-system sanity checks stay individually testable.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from unifideck.core.types import Result

if TYPE_CHECKING:
    from .models import DownloadItem

logger = logging.getLogger(__name__)

# Minimum free space (GB) required on the install volume.
# Below this, the download is refused with ``low_space:<x>GB``
# so the frontend can render a specific toast.
_MIN_FREE_GB = 1.0


def item_key(item: DownloadItem) -> str:
    """Return ``"<store>:<game_id>"`` — the queue's unique key.

    Used for de-dup checks in ``DownloadService.add`` and for
    progress-event coalescing at the dispatcher level.
    """
    return f"{item.store}:{item.game_id}"


def validate_path(path: str) -> Result:
    """Check that ``path`` is writable and has enough free space.

    Sequence: empty string → ``empty_path``; missing dir →
    ``mkdir -p`` (``mkdir_failed`` on OSError);
    ``os.access(W_OK)`` → ``not_writable``; ``statvfs`` free
    space < ``_MIN_FREE_GB`` → ``low_space:<x>GB``.
    ``statvfs`` failure is best-effort skip — we don't refuse
    a download just because we couldn't stat the volume (some
    FUSE mounts don't support it).

    Returns ``Result(success=True)`` on pass.
    """
    if not path:
        return Result(success=False, error="empty_path")

    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except OSError:
        return Result(success=False, error="mkdir_failed")

    if not os.access(path, os.W_OK):
        return Result(success=False, error="not_writable")

    try:
        st = os.statvfs(path)
        # Check free bytes available to non-root user (f_bavail * f_frsize)
        free_bytes = st.f_bavail * st.f_frsize
        free_gb = free_bytes / (1024**3)

        if free_gb < _MIN_FREE_GB:
            return Result(success=False, error=f"low_space:{free_gb:.1f}GB")
    except Exception as e:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
        # Best-effort skip: statvfs unsupported (Windows path?),
        # permission denied, or path missing. Treat as "no info"
        # rather than blocking the install.
        logger.debug("[downloads] statvfs check skipped: %s", e)

    return Result(success=True)
