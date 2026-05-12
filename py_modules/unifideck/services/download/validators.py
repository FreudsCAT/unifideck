"""Download validators — pure-function checks.

OP-15d | py_modules/unifideck/services/download/validators.py

Two pure functions :

* ``item_key(item)`` — the canonical key for a download item
  (``store:game_id``), used as the queue's de-dup primary key;
* ``validate_path(path)`` — sanity check the target path before
  starting a download (writable, enough free space, not inside a
  protected directory).
"""

from __future__ import annotations
import os
from pathlib import Path
from ...core.types import Result
from .models import DownloadItem


def item_key(item: DownloadItem) -> str:
    """Compute the canonical de-dup key for a download item.

    ``"<store>:<game_id>"`` — the same key the rest of the service
    uses to look up running downloads in ``_running`` and to reject
    duplicates in ``add``.

    Args:
        item: a ``DownloadItem``.

    Returns:
        Stable string key.
    """
    return f"{item.store}:{item.game_id}"


def validate_path(path: str) -> Result:
    """Pre-flight check the install path before queuing.

    Three checks in order:

    1. **Non-empty** — reject empty paths;
    2. **Directory exists or can be created** — try ``mkdir
       -p``, reject with ``mkdir_failed`` if the OS rejects it
       (permission denied, parent missing on a read-only mount);
    3. **Writable** — ``os.access(W_OK)``;
    4. **At least 1 GB free** — quick check via ``statvfs``;
       reject with ``low_space:<GB>`` below the threshold.
       ``statvfs`` failures (unusual filesystems) are tolerated
       and the check is skipped — better to attempt the install
       than to refuse on a stat quirk.

    Args:
        path: target install directory.

    Returns:
        ``Result(success=True)`` if the path passes every check,
        ``Result(success=False, error=…)`` with a specific error
        code otherwise.
    """
    if not path:
        return Result(success=False, error="empty_path")
    p = Path(path)
    if not p.is_dir():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return Result(
                success=False,
                error=f"mkdir_failed: {e}",
            )
    if not os.access(path, os.W_OK):
        return Result(success=False, error="not_writable")
    try:
        stat = os.statvfs(path)
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        if free_gb < 1.0:
            return Result(
                success=False,
                error=f"low_space:{free_gb:.1f}GB",
            )
    except OSError:
        pass
    return Result(success=True)
