"""Disk-backed TTL cache — per-namespace stores managed centrally.

OP-08k | py_modules/unifideck/core/cache_manager.py

Two cooperating types:

* ``CacheStore``   — one named cache backed by a single
  JSON file on disk. Holds ``data`` + per-key timestamps,
  applies a uniform TTL on read, writes are atomic via the
  ``temp + rename`` pattern plus a single-generation
  ``.bak`` snapshot for corruption recovery.
* ``CacheManager`` — the top-level facade. Owns a directory,
  lazy-registers named ``CacheStore`` instances inside it,
  and forwards CRUD calls to the named store.

Caches are intended for **derived / expensive** data —
metadata API results, store catalog snapshots, etc. — not for
authoritative state. The ``.bak`` recovery means a torn write
mid-shutdown loses at most one generation of cached data.

Files are written with ``chmod 0600`` so cache contents
(which may include user identifiers in keys) stay
owner-readable.
"""

import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CacheStore:
    """One named cache with TTL, disk persistence, and backup recovery."""

    def __init__(self, name: str, path: Path, ttl_seconds: int = 0) -> None:
        """Initialise the store and load existing data from disk.

        Args:
            name: identifier used in logs (e.g.
                ``"epic_library"``).
            path: JSON file path for this store.
            ttl_seconds: per-entry TTL. ``0`` (default)
                disables expiry — entries live until
                explicitly deleted.
        """
        self.name = name
        self.path = path
        self.ttl = ttl_seconds
        self._data: dict[str, Any] = {}
        self._ts: dict[str, float] = {}
        self._load()

    def get(self, key: str) -> Any | None:
        """Return the cached value for ``key`` or ``None`` if missing / expired.

        TTL check is lazy: expired entries are detected
        on read and evicted in place (so a follow-up
        ``set`` doesn't over-write a stale timestamp).
        TTL = 0 means "never expires" — return the value
        unconditionally.

        Args:
            key: cache key.

        Returns:
            Cached value, or ``None`` if not present or
            expired.
        """
        if key not in self._data:
            return None
        if self.ttl > 0:
            ts = self._ts.get(key, 0)
            if time.time() > (ts + self.ttl):
                self._data.pop(key, None)
                self._ts.pop(key, None)
                return None
        return self._data[key]

    def set(self, key: str, value: Any) -> None:
        """Insert/update ``key`` with ``value`` and persist immediately.

        Eager persist — each ``set`` triggers a full
        rewrite. Fine for the typical cache size
        (handful of keys, JSON-serialisable values);
        callers needing batch writes should debounce on
        their side.

        Args:
            key: cache key.
            value: any JSON-serialisable value.
        """
        self._data[key] = value
        self._ts[key] = time.time()
        self._save()

    def delete(self, key: str) -> None:
        """Drop ``key`` (and its timestamp) and persist.

        Idempotent: deleting a missing key is a no-op.

        Args:
            key: cache key.
        """
        self._data.pop(key, None)
        self._ts.pop(key, None)
        self._save()

    def clear(self) -> None:
        """Wipe every entry and persist the empty state.

        Used both for admin clears and for tests. The
        empty file is preserved (not deleted) so subsequent
        ``_load`` calls find a valid (empty) JSON.
        """
        self._data.clear()
        self._ts.clear()
        self._save()

    def size(self) -> int:
        """Return the entry count (excluding expired-but-not-yet-evicted).

        Note: expired entries are only evicted on
        ``get``; ``size`` returns the raw dict length so
        the count may transiently include expired keys.

        Returns:
            Current ``_data`` length.
        """
        return len(self._data)

    def _load(self) -> None:
        """Load cache contents from disk with backup fallback.

        Pipeline:

        1. Missing file → leave ``_data`` / ``_ts`` empty.
        2. Try the main file → on JSON / OS / ValueError,
           warn + fall through.
        3. Try the ``.bak`` file → on success, restore
           and immediately re-save to repair the main
           file.
        4. Both corrupt → log at ERROR, leave empty.

        Resilient by design: a corrupted cache file
        shouldn't break the plugin — at worst the cache
        starts cold.
        """
        if not self.path.exists():
            return
        try:
            raw = json.loads(
                self.path.read_text(encoding="utf-8"),
            )
            self._data = dict(raw.get("data", {}))
            self._ts = {k: float(v) for k, v in raw.get("_ts", {}).items()}
            return
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(
                "[CacheManager] %s corrupted (%s), trying backup",
                self.name,
                type(e).__name__,
            )
        bak = self.path.with_suffix(self.path.suffix + ".bak")
        if bak.exists():
            try:
                raw = json.loads(
                    bak.read_text(encoding="utf-8"),
                )
                self._data = dict(raw.get("data", {}))
                self._ts = {k: float(v) for k, v in raw.get("_ts", {}).items()}
                logger.info(
                    "[CacheManager] %s restored from backup",
                    self.name,
                )
                self._save()
                return
            except (json.JSONDecodeError, OSError, ValueError):
                logger.exception(
                    "[CacheManager] %s backup also corrupt",
                    self.name,
                )
        self._data = {}
        self._ts = {}

    def _save(self) -> None:
        """Atomically persist current state, snapshotting the prior file as ``.bak``.

        Five-step write:

        1. Ensure the parent directory exists.
        2. If the main file already exists, copy its
           bytes to ``.bak`` (single-generation backup).
        3. Write the new JSON to a ``.tmp`` sibling.
        4. ``replace`` the tmp file over the main path
           (atomic on POSIX).
        5. ``chmod 0600`` the result so cache contents
           are owner-readable only.

        Backup or chmod failures are logged but don't
        abort the write — the new data still lands. A
        write failure leaves the prior file untouched
        (atomic semantics).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"data": self._data, "_ts": self._ts}
        if self.path.exists():
            bak = self.path.with_suffix(
                self.path.suffix + ".bak",
            )
            try:
                bak.write_bytes(self.path.read_bytes())
            except OSError as e:
                logger.warning(
                    "[CacheManager] backup failed for %s: %s",
                    self.name,
                    e,
                )
        tmp = self.path.with_suffix(
            self.path.suffix + ".tmp",
        )
        try:
            tmp.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self.path)
            try:
                self.path.chmod(0o600)
            except OSError as e:
                logger.debug(
                    "[CacheManager] chmod %s failed: %s",
                    self.path,
                    e,
                )
        except OSError:
            logger.exception("[CacheManager] write failed for %s", self.name)
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()


class CacheManager:
    """Top-level facade owning a directory of named ``CacheStore`` instances."""

    def __init__(self, base_path: str) -> None:
        """Set up the cache directory and the (initially empty) store registry.

        The directory is created if missing —
        ``mkdir(parents=True, exist_ok=True)`` so missing
        parents don't crash the boot.

        Args:
            base_path: filesystem directory that will
                hold every per-store JSON file.
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, CacheStore] = {}

    def register(self, name: str, ttl_seconds: int = 0) -> None:
        """Register a new named cache (idempotent — repeat calls no-op).

        The file path is derived from ``name`` —
        ``<base>/<name>_cache.json``. The ``CacheStore``
        constructor loads any existing data from disk
        immediately so subsequent ``get`` calls find
        prior session data.

        Args:
            name: cache identifier (used in logs + file
                name).
            ttl_seconds: TTL in seconds; ``0`` = no
                expiry (default).
        """
        if name in self._stores:
            return
        path = self.base_path / f"{name}_cache.json"
        self._stores[name] = CacheStore(name, path, ttl_seconds)

    def _get_store(self, name: str) -> CacheStore:
        """Return the registered store or raise ``ValueError``.

        Strict lookup — unregistered caches raise instead
        of being silently created. Forces every cache to
        have an explicit ``register`` call at boot, which
        documents the cache surface and ensures TTLs are
        intentional.

        Args:
            name: cache identifier.

        Returns:
            The matching ``CacheStore``.

        Raises:
            ValueError: if no cache was registered under
                this name.
        """
        if name not in self._stores:
            raise ValueError(f"Cache {name!r} not registered")
        return self._stores[name]

    def get(self, cache: str, key: str) -> Any | None:
        """Forward a ``get`` to the named cache.

        Args:
            cache: cache identifier.
            key: key within that cache.

        Returns:
            Cached value or ``None``.
        """
        return self._get_store(cache).get(key)

    def set(self, cache: str, key: str, value: Any) -> None:
        """Forward a ``set`` to the named cache.

        Args:
            cache: cache identifier.
            key: key within that cache.
            value: any JSON-serialisable value.
        """
        self._get_store(cache).set(key, value)

    def delete(self, cache: str, key: str) -> None:
        """Forward a ``delete`` to the named cache.

        Args:
            cache: cache identifier.
            key: key within that cache.
        """
        self._get_store(cache).delete(key)

    def clear(self, cache: str) -> None:
        """Forward a ``clear`` to the named cache.

        Args:
            cache: cache identifier.
        """
        self._get_store(cache).clear()

    def clear_all(self) -> None:
        """Clear every registered cache.

        Used by admin "wipe caches" actions and by test
        teardown. Iterates the stores dict in dict-order
        (insertion order on CPython 3.7+).
        """
        for store in self._stores.values():
            store.clear()

    def cache_size(self, cache: str) -> int:
        """Return the entry count for the named cache.

        Args:
            cache: cache identifier.

        Returns:
            Entry count.
        """
        return self._get_store(cache).size()

    def registered_names(self) -> list[str]:
        """Return the list of registered cache names (insertion order).

        Used by diagnostics and admin UIs to enumerate
        what caches exist. Snapshot copy — caller can
        iterate without worrying about concurrent
        modifications.

        Returns:
            List of cache identifiers.
        """
        return list(self._stores.keys())
