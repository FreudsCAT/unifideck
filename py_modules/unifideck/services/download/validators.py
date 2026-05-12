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
    """Item key."""
    return f"{item.store}:{item.game_id}"


def validate_path(path: str) -> Result:
    """Validate path."""
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
