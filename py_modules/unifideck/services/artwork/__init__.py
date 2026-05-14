"""services/artwork — SteamGridDB artwork fetcher service.

Re-exports ``ArtworkService`` so callers can write
``from unifideck.services.artwork import ArtworkService``
rather than reaching into the private ``service`` submodule.
"""

from __future__ import annotations

from .service import ArtworkService

__all__ = ["ArtworkService"]
