"""core/cache_manager.py — Unified cache service.

# OP-04a | core/cache_manager.py | Depends: OP-05

Replaces 9 independent load/save function pairs with a single
generic registry. Each cache is named + TTL'd + atomic-written +
auto-backed-up on every save. Backup recovers from corrupt JSON.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class CacheStore:
    """Single named cache with TTL, atomic writes, backup recovery.

    On-disk layout: ``{"data": {key: value, ...}, "_ts": {key: epoch, ...}}``.
    TTL=0 means entries never expire.
    """

    def __init__(self, name: str, path: Path, ttl_seconds: int = 0) -> None:
        """Load from ``path`` if it exists; on corrupt JSON, fall back
        to ``<path>.bak`` then rewrite the main file from the backup.
        """
        raise NotImplementedError("OP-04a: implement load with corrupt JSON recovery")

    def get(self, key: str) -> Any | None:
        """Return value for ``key``, or None if missing or expired.
        Silently drops the entry when TTL has elapsed.
        """
        raise NotImplementedError("OP-04a: implement TTL-aware get")

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key`` and persist atomically."""
        raise NotImplementedError("OP-04a: implement atomic set + save")

    def delete(self, key: str) -> None:
        """Remove ``key`` (if present) and persist."""
        raise NotImplementedError("OP-04a: implement delete + save")

    def clear(self) -> None:
        """Empty the cache and persist."""
        raise NotImplementedError("OP-04a: implement clear + save")

    def size(self) -> int:
        """Return number of entries currently stored."""
        raise NotImplementedError("OP-04a: implement size")

    def _load(self) -> None:
        """Load from ``self._path``; recover from ``.bak`` if corrupt.
        If both are unusable, start empty. Never raises.
        """
        raise NotImplementedError("OP-04a: implement load with bak fallback")

    def _save(self) -> None:
        """Persist atomically: backup main file → write tmp → rename.
        Chmod 0o600 after write (cache files contain OAuth tokens).
        Best-effort — logs errors but never raises.
        """
        raise NotImplementedError("OP-04a: implement atomic save with backup")


class CacheManager:
    """Registry of named ``CacheStore`` instances (Layer 2 singleton).

    Usage::

        cm = CacheManager("/home/deck/.local/share/unifideck/cache")
        cm.register("steam_metadata", ttl_seconds=86400)
        cm.set("steam_metadata", "123456", {"name": "Hades"})
    """

    def __init__(self, base_path: str) -> None:
        """Create ``base_path`` directory if missing, init empty registry."""
        raise NotImplementedError("OP-04a: create base_path, init registry dict")

    def register(self, name: str, ttl_seconds: int = 0) -> None:
        """Register a named cache. Idempotent — second call is a no-op."""
        raise NotImplementedError("OP-04a: create CacheStore at base_path/name.json")

    def _get_store(self, name: str) -> CacheStore:
        """Return the named store or raise ``ValueError`` if unregistered."""
        raise NotImplementedError("OP-04a: lookup in registry, raise if missing")

    def get(self, cache: str, key: str) -> Any | None:
        """Forward to ``self._get_store(cache).get(key)``."""
        raise NotImplementedError("OP-04a: delegate to CacheStore.get")

    def set(self, cache: str, key: str, value: Any) -> None:
        """Forward to ``self._get_store(cache).set(key, value)``."""
        raise NotImplementedError("OP-04a: delegate to CacheStore.set")

    def delete(self, cache: str, key: str) -> None:
        """Forward to ``self._get_store(cache).delete(key)``."""
        raise NotImplementedError("OP-04a: delegate to CacheStore.delete")

    def clear(self, cache: str) -> None:
        """Empty the named cache. Raises if unregistered."""
        raise NotImplementedError("OP-04a: delegate to CacheStore.clear")

    def clear_all(self) -> None:
        """Empty every registered cache in place."""
        raise NotImplementedError("OP-04a: iterate registry, call clear on each")

    def cache_size(self, cache: str) -> int:
        """Return entry count of the named cache."""
        raise NotImplementedError("OP-04a: delegate to CacheStore.size")

    def registered_names(self) -> list[str]:
        """Return list of registered cache names."""
        raise NotImplementedError("OP-04a: return list(self._registry.keys())")
