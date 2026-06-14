"""SteamGridDB package — multi-file replacement for the legacy ``steamgriddb.py``.

Public API matches the legacy single-file module so existing imports
work without churn:

* ``from unifideck.steam import steamgriddb``
* ``from unifideck.steam.steamgriddb import SteamGridDBClient``

Internal layout:

* :mod:`constants` — API base, kind→endpoint map, dimension/style
  defaults + relaxed fallbacks, style priority, publisher prefixes.
* :mod:`match` — re-export shim; the title normalisation /
  edition-suffix stripping / Jaccard scoring primitives now live in
  the shared :mod:`unifideck.utils.title_match`.
* :mod:`search` — 6-pass title→game_id ladder.
* :mod:`assets` — :class:`ArtworkAsset` dataclass + filtered
  ``fetch_assets`` + ``fetch_with_fallback``.
* :mod:`ranking` — 5-level asset ranking + ``pick_best``.
* :mod:`batch` — one search + parallel kind fetch.
* :mod:`client` — :class:`SteamGridDBClient` + free functions.
"""
from __future__ import annotations

from .assets import ArtworkAsset
from .client import (
    SteamGridDBClient,
    fetch_all_kinds,
    search_artwork,
)
from .constants import ARTWORK_KINDS, SGDB_API_BASE

__all__ = [
    "ARTWORK_KINDS",
    "SGDB_API_BASE",
    "ArtworkAsset",
    "SteamGridDBClient",
    "fetch_all_kinds",
    "search_artwork",
]
