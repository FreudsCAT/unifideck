"""Download service — multi-store download queue.

OP-15 | py_modules/unifideck/services/download/__init__.py

Re-exports ``DownloadService`` (the orchestration class) and
``DownloadItem`` (the per-download record).

The service multiplexes downloads across stores : each store's
installer (gogdl, nile, legendary) reports progress through the
same uniform progress callback, which the download service routes
to the per-item state machine and emits progress events on the bus.
"""

from __future__ import annotations
from .models import DownloadItem, classify_download_error
from .service import DownloadService

__all__ = [
    "DownloadItem",
    "DownloadService",
    "classify_download_error",
]
