"""core/cache_manager.py — Unified cache service.

# OP-04a | core/cache_manager.py | Depends: OP-05

Replaces 9 independent load/save function pairs with a single
generic registry. Each cache is named + TTL'd + atomic-written +
auto-backed-up on every save. Backup recovers from corrupt JSON.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CacheStore:
    """Single named cache with TTL, atomic writes, backup recovery.

    On-disk layout: ``{"data": {key: value, ...}, "_ts": {key: epoch, ...}}``.
    TTL=0 means entries never expire.
    """

    def __init__(self, name: str, path: Path, ttl_seconds: int = 0) -> None:
        """Load from ``path`` if it exists; on corrupt JSON, fall back
        to ``<path>.bak`` then rewrite the main file from the backup.
        """
        self._name = name
        self._path = path
        self._ttl = ttl_seconds
        self._data: dict[str, Any] = {}
        self._ts: dict[str, float] = {}
        self._load()

    def get(self, key: str) -> Any | None:
        """Return value for ``key``, or None if missing or expired.
        Silently drops the entry when TTL has elapsed.
        """
        if key not in self._data:
            return None
        if self._ttl > 0:
            stored_at = self._ts.get(key, 0)
            if time.time() - stored_at > self._ttl:
                del self._data[key]
                self._ts.pop(key, None)
                self._save()
                return None
        return self._data[key]

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key`` and persist atomically."""
        self._data[key] = value
        self._ts[key] = time.time()
        self._save()

    def delete(self, key: str) -> None:
        """Remove ``key`` (if present) and persist."""
        if key in self._data:
            del self._data[key]
            self._ts.pop(key, None)
            self._save()

    def clear(self) -> None:
        """Empty the cache and persist."""
        self._data.clear()
        self._ts.clear()
        self._save()

    def size(self) -> int:
        """Return number of entries currently stored."""
        return len(self._data)

    def _load(self) -> None:
        """Load from ``self._path``; recover from ``.bak`` if corrupt.
        If both are unusable, start empty. Never raises.
        """
        data = self._try_load_file(self._path)
        if data is not None:
            self._data = data.get("data", {})
            self._ts = data.get("_ts", {})
            return

        # Main file corrupt or missing — try backup
        bak_path = Path(str(self._path) + ".bak")
        data = self._try_load_file(bak_path)
        if data is not None:
            logger.warning("[Cache:%s] Recovered from backup: %s", self._name, bak_path)
            self._data = data.get("data", {})
            self._ts = data.get("_ts", {})
            # Rewrite main file from backup
            self._save()
            return

        # Both unusable — start empty
        self._data = {}
        self._ts = {}

    @staticmethod
    def _try_load_file(path: Path) -> dict[str, Any] | None:
        """Try to load and parse a JSON file. Return None on any failure."""
        try:
            if not path.exists():
                return None
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "data" in data:
                return data
            return None
        except Exception:
            return None

    def _save(self) -> None:
        """Persist atomically: backup main file → write tmp → rename.
        Chmod 0o600 after write (cache files contain OAuth tokens).
        Best-effort — logs errors but never raises.
        """
        p = str(self._path)
        tmp = p + ".tmp"
        bak = p + ".bak"
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)

            # Backup existing main file before overwrite
            if os.path.exists(p):
                try:
                    shutil.copy2(p, bak)
                except OSError as e:
                    logger.debug("[Cache:%s] Backup failed (non-fatal): %s", self._name, e)

            # Atomic write: tmp → fsync → chmod → rename
            payload = {"data": self._data, "_ts": self._ts}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.rename(tmp, p)

        except Exception as e:
            logger.error("[Cache:%s] Save failed: %s", self._name, e)
            try:
                os.unlink(tmp)
            except OSError:
                pass


class CacheManager:
    """Registry of named ``CacheStore`` instances (Layer 2 singleton).

    Usage::

        cm = CacheManager("/home/deck/.local/share/unifideck/cache")
        cm.register("steam_metadata", ttl_seconds=86400)
        cm.set("steam_metadata", "123456", {"name": "Hades"})
    """

    def __init__(self, base_path: str) -> None:
        """Create ``base_path`` directory if missing, init empty registry."""
        self._base = Path(base_path)
        os.makedirs(str(self._base), exist_ok=True)
        self._registry: dict[str, CacheStore] = {}

    def register(self, name: str, ttl_seconds: int = 0) -> None:
        """Register a named cache. Idempotent — second call is a no-op."""
        if name not in self._registry:
            store_path = self._base / f"{name}.json"
            self._registry[name] = CacheStore(name, store_path, ttl_seconds)

    def _get_store(self, name: str) -> CacheStore:
        """Return the named store or raise ``ValueError`` if unregistered."""
        if name not in self._registry:
            raise ValueError(f"Cache '{name}' is not registered")
        return self._registry[name]

    def get(self, cache: str, key: str) -> Any | None:
        """Forward to ``self._get_store(cache).get(key)``."""
        return self._get_store(cache).get(key)

    def set(self, cache: str, key: str, value: Any) -> None:
        """Forward to ``self._get_store(cache).set(key, value)``."""
        self._get_store(cache).set(key, value)

    def delete(self, cache: str, key: str) -> None:
        """Forward to ``self._get_store(cache).delete(key)``."""
        self._get_store(cache).delete(key)

    def clear(self, cache: str) -> None:
        """Empty the named cache. Raises if unregistered."""
        self._get_store(cache).clear()

    def clear_all(self) -> None:
        """Empty every registered cache in place."""
        for store in self._registry.values():
            store.clear()

    def cache_size(self, cache: str) -> int:
        """Return entry count of the named cache."""
        return self._get_store(cache).size()

    def registered_names(self) -> list[str]:
        """Return list of registered cache names."""
        return list(self._registry.keys())
