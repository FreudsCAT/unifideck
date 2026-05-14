"""bootstrap.cache_registry — declare every named cache used by the plugin.

Called during ``_main`` BEFORE ``auto_discover()`` runs the
store constructors, because some stores call ``is_available()``
during construction which reads from the cache. Missing the
registration would cause a ``KeyError`` the first time a store
asks for its cache slot.

The TTL table is the single source of truth for which caches
exist and how long their entries survive. TTL semantics (from
``CacheManager``):

  - ``0`` — unbounded lifetime, entry survives until explicit
    invalidation (used for IDs and maps that don't expire by
    time)
  - positive integer — seconds until entry becomes stale

Four stores (epic, gog, amazon, microsoft, ubisoft) also get
one cache slot each, all TTL=0 — they're used to memoize the
per-store ``is_available`` result inside a single plugin session
so we don't re-probe every RPC call.
"""
from __future__ import annotations

from typing import Any

# Cache spec: (name, ttl_seconds). 0 means unbounded.
# Centralised so adding a cache = appending one tuple; no need
# to edit the bootstrap orchestrator.
_NAMED_CACHES: tuple[tuple[str, int], ...] = (
    ("steam_appid", 0),
    ("steam_real_appid", 0),
    ("steam_metadata", 86400),
    ("rawg_metadata", 86400),
    ("unifidb_metadata", 86400),
    ("metacritic", 604800),
    ("artwork_attempts", 0),
    ("game_sizes", 3600),
    ("compat", 0),
    # ``MetadataService`` caches the merged-and-deduped metadata
    # under the ``"metadata"`` namespace (see ``CACHE_NAMESPACE``
    # in ``services/metadata_service.py``). Earlier this slot was
    # missing from the registry — ``_get_store("metadata")`` raised
    # ``ValueError: Cache 'metadata' not registered``, swallowed
    # by the service's try/except, so every ``enrich()`` call
    # silently re-fetched from all three upstream sources.
    # 7 days mirrors the service's ``DEFAULT_CACHE_TTL``.
    ("metadata", 7 * 24 * 3600),
)

_STORE_CACHES: tuple[str, ...] = (
    "epic", "gog", "amazon", "microsoft", "ubisoft",
)


def register_default_caches(cache: Any) -> None:
    """Declare every named + per-store cache on ``cache``.

    Args:
        cache: The ``CacheManager`` instance the Plugin holds on
            ``self.cache``. Mutated in place — every cache slot
            is registered via ``cache.register(name, ttl_seconds=N)``.

    Must be called before any store's constructor runs; see the
    module docstring for the ordering rationale.
    """
    for name, ttl in _NAMED_CACHES:
        cache.register(name, ttl_seconds=ttl)
    for store_name in _STORE_CACHES:
        cache.register(store_name, ttl_seconds=0)
