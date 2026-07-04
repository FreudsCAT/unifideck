"""services/download — Background install queue service.

Re-exports ``DownloadService`` so callers can write
``from unifideck.services.download import DownloadService``
rather than reaching into the private ``service`` submodule.
"""

from __future__ import annotations

from .service import DownloadService

__all__ = ["DownloadService"]
