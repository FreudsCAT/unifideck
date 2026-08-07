"""Short-lived cache for per-store "which games have updates" scans.

py_modules/unifideck/services/update_check_cache.py

``StoreBase.check_for_updates()`` is a **bulk** call — it takes no
arguments and returns every updatable game id for that store. The only
caller, ``check_game_update(store, game_id)``, is a **point** query: it
runs the whole scan and keeps one boolean. The App-Details Play section
fires that query on every mount of an installed game, so without a cache
each page open costs:

* **Epic** — a ``legendary list-installed --check-updates`` subprocess,
  which logs in to Epic and re-downloads the asset manifest for every
  installed platform (60 s timeout);
* **GOG** — one sequential HTTPS request to ``content-system.gog.com``
  per installed game (10 s timeout each);
* **Amazon** — a ``nile list-updates`` subprocess.

Caching the bulk result turns "open five game pages" into one scan.

**In-memory, not** :class:`~unifideck.core.cache_manager.CacheManager`:
update availability is a fact about the network right now, not something
that should survive a plugin restart as truth. A process-lifetime cache
self-heals on restart for free, and there is nothing here worth the
write amplification of a disk cache.

Concurrency: waiters on the same store share a single in-flight scan via
a per-store lock, so two page opens a millisecond apart cannot spawn two
legendary logins.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

# How long a scan result is trusted.
#
# Deliberately LONGER than ``UpdateSweepService.POLL_INTERVAL_SECONDS``
# (6 h): the sweep is what keeps this fresh, and the TTL is only the
# safety net for when it isn't running (wedged task, a build with the
# sweep disabled). A TTL shorter than the sweep interval would put the
# 5-10 s legendary login back in front of the user between sweeps, which
# is the whole thing the sweep exists to remove.
TTL_S = 7 * 3600

# store -> (game ids with updates, monotonic timestamp of the scan)
_CACHE: dict[str, tuple[list[str], float]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


def _fresh(store: str, ttl: float) -> list[str] | None:
    """Return the cached scan for ``store``, or ``None`` when stale/absent."""
    entry = _CACHE.get(store)
    if entry is None:
        return None
    value, stamped = entry
    if time.monotonic() - stamped >= ttl:
        return None
    return value


def _lock_for(store: str) -> asyncio.Lock:
    """Return the per-store lock, creating it on first use."""
    lock = _LOCKS.get(store)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[store] = lock
    return lock


async def get_or_fetch(
    store: str,
    fetch: Callable[[], Awaitable[list[str]]],
    *,
    ttl: float = TTL_S,
) -> list[str]:
    """Return ``store``'s updatable-game ids, scanning at most once per ``ttl``.

    Args:
      store: store id (``"epic"``, ``"gog"``, …) — the cache key.
      fetch: coroutine performing the real bulk scan. Only awaited on a
        miss; exceptions propagate to the caller and are NOT cached, so a
        crashed scan is retried on the next call rather than pinned for
        the whole TTL.
      ttl: override the default window (tests, mainly).

    Returns:
      A copy of the cached list — callers must not be able to mutate the
      cache by mutating what they got back.
    """
    hit = _fresh(store, ttl)
    if hit is not None:
        return list(hit)
    async with _lock_for(store):
        # Re-check under the lock: whoever we queued behind has just
        # filled the cache, and running a second scan would defeat the
        # point of holding the lock at all.
        hit = _fresh(store, ttl)
        if hit is not None:
            return list(hit)
        value = list(await fetch())
        _CACHE[store] = (value, time.monotonic())
        return list(value)


def peek(store: str, *, ttl: float = TTL_S) -> list[str] | None:
    """Return ``store``'s cached scan, or ``None`` on a miss.

    Never scans. This is what the RPC path uses: answering a page open
    must not block on an Epic login, so a miss returns ``None`` and the
    caller schedules a background refresh instead of waiting for one.
    """
    hit = _fresh(store, ttl)
    return list(hit) if hit is not None else None


def invalidate(store: str) -> None:
    """Drop ``store``'s cached scan so the next query re-scans.

    Called when an update is queued: the button must not keep offering an
    update that is already downloading, and once it lands the game's
    version has changed under us.
    """
    _CACHE.pop(store, None)


def clear() -> None:
    """Drop every cached scan (test isolation, full-cleanup flows)."""
    _CACHE.clear()
