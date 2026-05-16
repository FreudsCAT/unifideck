"""services/cloud_save — Cloud-save sync service.

Re-exports ``CloudSaveService`` so callers can write
``from unifideck.services.cloud_save import CloudSaveService``
rather than reaching into the private ``service`` submodule.
"""

from __future__ import annotations

from .service import CloudSaveService

__all__ = ["CloudSaveService"]
