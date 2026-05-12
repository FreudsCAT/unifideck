"""Artwork service — game artwork fetcher + cache.

OP-16 | py_modules/unifideck/services/artwork/__init__.py

Re-exports ``ArtworkService``. The service fetches game capsules
(library cover), heroes (page hero), logos, and icons from
SteamGridDB and caches them on disk for offline display.
"""

from __future__ import annotations
from .service import ArtworkService

__all__ = ["ArtworkService"]
