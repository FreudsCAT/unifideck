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

# Minimum free space (GB) required on the install volume when the
# game's real size is unknown. Below this, the download is refused
# with ``low_space:<x>GB`` so the frontend can render a specific toast.
_MIN_FREE_GB = 1.0

# When the game's download size *is* known, the refusal instead carries
# both numbers as ``insufficient_space:need=<N>GB,free=<F>GB`` (GiB, one
# decimal — matching the ``low_space`` format) so the frontend can render
# an actionable "needs X, only Y free" message instead of a bare code.


def item_key(item: DownloadItem) -> str:
    """Return ``"<store>:<game_id>"`` — the queue's unique key.

    Used for de-dup checks in ``DownloadService.add`` and for
    progress-event coalescing at the dispatcher level.
    """
    return f"{item.store}:{item.game_id}"


def validate_path(path: str, required_bytes: int | None = None) -> Result:
    """Check that ``path`` is writable and has enough free space.

    Sequence: empty string → ``empty_path``; missing dir →
    ``mkdir -p`` (``mkdir_failed`` on OSError);
    ``os.access(W_OK)`` → ``not_writable``; then the free-space
    check on ``statvfs``:

    * when ``required_bytes`` is a positive size, refuse if the
      volume can't hold it → ``insufficient_space:need=<N>GB,free=<F>GB``
      (the more informative code — checked first so a full volume that
      also can't fit the game names the game's requirement);
    * otherwise fall back to the static floor: free < ``_MIN_FREE_GB``
      → ``low_space:<x>GB``.

    ``required_bytes`` is ``None`` (unknown size) → only the floor
    applies, so an unknown size never blocks beyond today's behaviour.
    ``statvfs`` failure is best-effort skip — we don't refuse a
    download just because we couldn't stat the volume (some FUSE
    mounts don't support it).

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

        if required_bytes and required_bytes > 0 and free_bytes < required_bytes:
            need_gb = required_bytes / (1024**3)
            return Result(
                success=False,
                error=f"insufficient_space:need={need_gb:.1f}GB,free={free_gb:.1f}GB",
            )
        if free_gb < _MIN_FREE_GB:
            return Result(success=False, error=f"low_space:{free_gb:.1f}GB")
    except Exception as e:
        # Best-effort skip: statvfs unsupported (Windows path?),
        # permission denied, or path missing. Treat as "no info"
        # rather than blocking the install.
        logger.debug("[downloads] statvfs check skipped: %s", e)

    return Result(success=True)
