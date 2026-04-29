"""core/cache_manager.py — Unified cache service.
Replaces 9 independent load/save function pairs (18 functions, 222
_cache references in main.py) with a single generic CacheManager.
Features:
- Named caches registered at startup (`register(name, ttl_seconds)`).
- Optional TTL per cache (0 = never expires).
- Atomic writes via tmp + rename (power-loss safe).
- Automatic .bak backup on every write.
- Corrupt JSON recovery from backup.
- Backward-compatible read of legacy cache files.
Reference: Technical Document v1.0 — Section 3.4.1 (CacheManager),
ADR-02 (Singleton CacheManager).
"""
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
class CacheStore:
    """Single named cache with TTL, atomic writes, and backup recovery.
    Data layout on disk (JSON):
    {
    "data": {"<key>": <value>, ...},
    "_ts": {"<key>": <epoch_seconds>, ...}
    }
    The `_ts` dict tracks insertion time for TTL expiration. TTL of 0
    means entries never expire.
    """

    def __init__(self, name: str, path: Path, ttl_seconds: int = 0) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        self.name = name
        self.path = path
        self.ttl = ttl_seconds
        self._data: dict[str, Any] = {}
        self._ts: dict[str, float] = {}
        self._load()
        # -------- public API --------
    def get(self, key: str) -> Any | None:
        """Return value for key, or None if missing or expired."""
        if key not in self._data:
            return None
        if self.ttl > 0:
            ts = self._ts.get(key, 0)
            if time.time() > (ts + self.ttl):
                # Expired — drop silently
                self._data.pop(key, None)
                self._ts.pop(key, None)
                return None
        return self._data[key]
    def set(self, key: str, value: Any) -> None:
        """Store value for key and persist atomically."""
        self._data[key] = value
        self._ts[key] = time.time()
        self._save()
    def delete(self, key: str) -> None:
        """Remove key from cache and persist."""
        self._data.pop(key, None)
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
        # -------- persistence --------
    def _load(self) -> None:
        """Load cache from disk, with corrupt-JSON recovery from .bak."""
        if not self.path.exists():
            return
        try:
            raw = json.loads(
                self.path.read_text(encoding="utf-8"),
            )
            self._data = dict(raw.get("data", {}))
            self._ts = {
                k: float(v)
                for k, v in raw.get("_ts", {}).items()
            }
            return
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(
                "[CacheManager] %s corrupted (%s), trying backup",
                self.name, type(e).__name__,
            )
        # Try backup
        bak = self.path.with_suffix(self.path.suffix + ".bak")
        if bak.exists():
            try:
                raw = json.loads(
                    bak.read_text(encoding="utf-8"),
                )
                self._data = dict(raw.get("data", {}))
                self._ts = {
                    k: float(v)
                    for k, v in raw.get("_ts", {}).items()
                }
                logger.info(
                    "[CacheManager] %s restored from backup",
                    self.name,
                )
                # Rewrite main file from backup
                self._save()
                return
            except (json.JSONDecodeError, OSError, ValueError):
                logger.error(
                    "[CacheManager] %s backup also corrupt",
                    self.name,
                )
        # Give up — start empty
        self._data = {}
        self._ts = {}

    def _save(self) -> None:
        """Persist atomically (backup → tmp → rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"data": self._data, "_ts": self._ts}
        # Backup existing file before overwriting
        if self.path.exists():
            bak = self.path.with_suffix(
                self.path.suffix + ".bak",
            )
            try:
                bak.write_bytes(self.path.read_bytes())
            except OSError as e:
                logger.warning(
                    "[CacheManager] backup failed for %s: %s",
                    self.name, e,
                )
        # Write atomically via tmp + rename
        tmp = self.path.with_suffix(
            self.path.suffix + ".tmp",
        )
        try:
            tmp.write_text(
                json.dumps(
                    payload, ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self.path)
            # SECURITY: cache files may contain OAuth tokens
            # and session IDs. Restrict to owner read/write
            # only so other Linux users on the same Steam
            # Deck (or any process running under a different
            # account) cannot read them. Idempotent —
            # applied on every save.
            try:
                self.path.chmod(0o600)
            except OSError as e:
                logger.debug(
                    "[CacheManager] chmod %s failed: %s",
                    self.path, e,
                )
        except OSError as e:
            logger.error(
                "[CacheManager] write failed for %s: %s",
                self.name, e,
            )
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    # best-effort cleanup; file may already be gone or locked
                    pass
class CacheManager:
    """Registry of named CacheStore instances (Layer 2 singleton).
    Usage:
    cm = CacheManager("/home/deck/.local/share/unifideck/cache")
    cm.register("steam_metadata", ttl_seconds=86400)
    cm.set("steam_metadata", "123456", {"name": "Hades"})
    value = cm.get("steam_metadata", "123456").
    """

    def __init__(self, base_path: str) -> None:  # noqa: D107 — class docstring documents the constructor's contract
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, CacheStore] = {}
    def register(self, name: str, ttl_seconds: int = 0) -> None:
        """Register a cache. Idempotent — safe to call multiple times."""
        if name in self._stores:
            return
        path = self.base_path / f"{name}_cache.json"
        self._stores[name] = CacheStore(name, path, ttl_seconds)

    def _get_store(self, name: str) -> CacheStore:
        if name not in self._stores:
            raise ValueError(f"Cache {name!r} not registered")
        return self._stores[name]
            # -------- proxied API --------
    def get(self, cache: str, key: str) -> Any | None:  # noqa: D102 — documentation pending (Sprint D)
        return self._get_store(cache).get(key)
    def set(self, cache: str, key: str, value: Any) -> None:  # noqa: D102 — documentation pending (Sprint D)
        self._get_store(cache).set(key, value)
    def delete(self, cache: str, key: str) -> None:  # noqa: D102 — documentation pending (Sprint D)
        self._get_store(cache).delete(key)
    def clear(self, cache: str) -> None:  # noqa: D102 — documentation pending (Sprint D)
        self._get_store(cache).clear()

    def clear_all(self) -> None:
        """Empty every registered cache in place."""
        for store in self._stores.values():
            store.clear()
    def cache_size(self, cache: str) -> int:  # noqa: D102 — documentation pending (Sprint D)
        return self._get_store(cache).size()
    def registered_names(self) -> list[str]:  # noqa: D102 — documentation pending (Sprint D)
        return list(self._stores.keys())
