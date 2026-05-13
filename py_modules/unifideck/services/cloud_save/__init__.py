"""Cloud save service — Steam Cloud + native cloud sync.

OP-17 | py_modules/unifideck/services/cloud_save/__init__.py

Re-exports ``CloudSaveService``. The service handles game-save
synchronisation between the local Steam Deck and Steam Cloud /
store-specific cloud (GOG Galaxy cloud, Epic cloud, etc.).
"""

from __future__ import annotations
from .service import CloudSaveService

__all__ = ["CloudSaveService"]
