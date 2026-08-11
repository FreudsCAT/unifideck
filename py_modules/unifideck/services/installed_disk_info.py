"""Per-game disk facts for the QAM "Installed" list.

py_modules/unifideck/services/installed_disk_info.py

Answers two questions for every *installed* game in one round trip:

* **How big is it on disk** — the exact directory walk shared with
  App-Details (:func:`~unifideck.stores.shared.installed_size.installed_size_bytes`).
* **Where does it live** — ``internal`` or ``external``.

Why a bulk collector rather than N per-row calls to
``get_game_size_bytes``: an installed size is an uncached recursive
``scandir`` walk (:mod:`unifideck.services.size_cache` deliberately
excludes installed games, since the number moves with saves/updates/DLC).
Firing one walk per row every time the Quick-Access panel opens would
mean a dozen concurrent walks over tens of gigabytes — on a Deck that is
disk contention the user feels while a game is running. Here the walks
are semaphore-bounded and memoised for :data:`MEMO_TTL_S`, so a repeat
panel open costs nothing.

Best-effort throughout: a game whose size or location cannot be resolved
is simply omitted from the result, so the row renders without a meta
line rather than the whole list failing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from unifideck.core.types.domain import Game
from unifideck.stores.shared.installed_size import (
    dir_size_bytes,
    resolve_size_root,
)
from unifideck.utils import mounts

logger = logging.getLogger(__name__)

# Concurrent directory walks. Two, not "as many as there are games": the
# walks are I/O-bound on the same device, so more of them makes the whole
# batch slower AND steals bandwidth from a running game. Two keeps one
# walk queued behind the other so the disk stays busy without thrashing.
WALK_CONCURRENCY = 2

# How long a computed (size, location) pair is served from memory. An
# install's size only changes on update/DLC/save writes, and the panel is
# opened repeatedly in a session — five minutes makes every open after
# the first instant while still tracking a game that grew.
MEMO_TTL_S = 300.0

# ``(store, store_game_id, install_path)`` → ``(monotonic_stamp, entry)``.
# Process-wide: every caller is the same singleton plugin, and the walk is
# expensive enough to be worth sharing.
_memo: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def clear_memo() -> None:
    """Drop every memoised entry (used by tests and after a full wipe)."""
    _memo.clear()


def classify_location(install_path: str | None) -> str | None:
    """Return ``"internal"``, ``"external"``, or ``None`` for *install_path*.

    Internal means "on the same filesystem device as ``~``" — the exact
    definition :func:`unifideck.utils.mounts.scan_mounts` already uses to
    decide what counts as an external device, so the two can never
    disagree about a given drive.

    ``~`` is resolved with :func:`os.path.expanduser`, matching
    ``rpc/mixins/storage.py``'s ``_build_storage_locations``. That is the
    home the installer itself writes ``~/Games`` against, so the label
    describes where the bytes actually went regardless of what ``HOME``
    the backend process happens to run with.

    ``None`` when the path is empty or either ``stat`` fails
    (:func:`~unifideck.utils.mounts.stat_dev` returns ``0``) — an unknown
    location is reported as absent rather than guessed as internal.
    """
    if not install_path:
        return None
    dev = mounts.stat_dev(install_path)
    home_dev = mounts.stat_dev(os.path.expanduser("~"))
    if not dev or not home_dev:
        return None
    return "internal" if dev == home_dev else "external"


def _installed(games: Any) -> list[Game]:
    """Games with the install flag set and enough identity to key on."""
    out: list[Game] = []
    for game in games or []:
        if not getattr(game, "installed", False):
            continue
        if not getattr(game, "store", "") or not getattr(game, "store_game_id", ""):
            continue
        out.append(game)
    return out


def _cached(key: tuple[str, str, str], now: float) -> dict[str, Any] | None:
    """Return the live memo entry for *key*, evicting it once expired."""
    hit = _memo.get(key)
    if hit is None:
        return None
    stamp, entry = hit
    if now - stamp > MEMO_TTL_S:
        del _memo[key]
        return None
    return entry


async def _resolve_one(
    registry: Any, sem: asyncio.Semaphore, game: Game,
) -> tuple[str, dict[str, Any]] | None:
    """Resolve one game's ``(key, {size_bytes, location})``. Never raises.

    Returns ``None`` when nothing useful could be resolved — a size of
    ``0`` *and* an unknown location carries no information, so it is left
    out of the map entirely.
    """
    store = game.store
    game_id = game.store_game_id
    install_path = game.install_path or ""
    key = f"{store}:{game_id}"
    memo_key = (store, game_id, install_path)
    now = time.monotonic()
    cached = _cached(memo_key, now)
    if cached is not None:
        return key, cached

    adapter: Any = None
    try:
        adapter = registry.get_store(store) if registry is not None else None
    except Exception:
        adapter = None

    resolved: str | None = None
    size = 0
    async with sem:
        try:
            # Resolve once, then size AND classify that same directory. A
            # game whose cached ``install_path`` went stale (moved to the SD
            # card, re-installed elsewhere) resolves through the store's own
            # records — classifying the cached path instead would report the
            # dead location next to a live size. For a wrapper store that
            # directory is the prefix, which is also the right thing to
            # classify: relocating the prefix IS relocating the game.
            resolved = await resolve_size_root(
                adapter, install_path, game_id, store,
            )
            if resolved is not None:
                size = await asyncio.to_thread(dir_size_bytes, resolved)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "[InstalledDiskInfo] size walk failed for %s", key, exc_info=True,
            )
            return None
    location = classify_location(resolved)
    if not size and location is None:
        return None
    entry: dict[str, Any] = {"size_bytes": int(size or 0), "location": location}
    _memo[memo_key] = (time.monotonic(), entry)
    return key, entry


async def collect_installed_disk_info(
    games: Any, registry: Any,
) -> dict[str, dict[str, Any]]:
    """Map ``"<store>:<store_game_id>"`` → ``{size_bytes, location}``.

    Keyed on ``store_game_id`` (not ``Game.id``) to match the frontend's
    shortcut cache, which is keyed the same way — see
    ``resolveAppIdFromStoreGame`` in ``src/lib/library-filters``.

    Only installed games appear. Entries that resolve to neither a size
    nor a location are omitted, as is any game whose lookup raised.
    """
    pending = _installed(games)
    if not pending:
        return {}
    sem = asyncio.Semaphore(WALK_CONCURRENCY)
    results = await asyncio.gather(
        *(_resolve_one(registry, sem, g) for g in pending),
        return_exceptions=True,
    )
    out: dict[str, dict[str, Any]] = {}
    for result in results:
        if isinstance(result, tuple):
            out[result[0]] = result[1]
    return out
