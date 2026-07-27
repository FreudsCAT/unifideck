"""services/shortcut/persistence.py — Atomic I/O for shortcuts.vdf + games.map.

Pure async helpers extracted from ``ShortcutService`` so the
orchestrator stays focused on the public API while I/O mechanics
(retry-on-corruption, tmpfile+os.replace) stay independently
testable.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any

import vdf

from .games_map import GameMapEntry, format_games_map, parse_games_map
from .orphan_scan import _is_launcher_exe

logger = logging.getLogger(__name__)

# Games.map read retries — 3 x 100ms worst-case. Cheap enough to
# avoid spurious GameNotFoundError when the launcher reads
# mid-write by a concurrent background sync.
_GAMES_MAP_READ_ATTEMPTS = 3
_GAMES_MAP_RETRY_DELAY_S = 0.1


async def read_vdf(shortcuts_path: str) -> dict[str, Any]:
    """Load shortcuts.vdf into a dict (empty dict if missing).

    Offloaded via ``to_thread`` since the vdf library is sync.
    """
    if not await asyncio.to_thread(lambda: Path(shortcuts_path).is_file()):
        return {"shortcuts": {}}

    def _read_sync() -> dict[str, Any]:
        try:
            with Path(shortcuts_path).open("rb") as f:
                return vdf.binary_loads(f.read())  # type: ignore[no-any-return,no-untyped-call]  # vdf.binary_loads is untyped + returns Any
        except Exception as e:
            logger.warning("[ShortcutPersistence] failed to read shortcuts.vdf: %s", e)
            return {"shortcuts": {}}

    return await asyncio.to_thread(_read_sync)


async def write_vdf(shortcuts_path: str, data: dict[str, Any]) -> None:
    """Persist shortcuts.vdf atomically, keeping the file executable.

    Uses tmpfile + os.replace to prevent corruption on crash.

    The final ``os.chmod(..., 0o755)`` is load-bearing for coexistence
    with **NonSteamLaunchers (NSL)**. NSL's persistent
    ``nslgamescanner.service`` treats the *executable bit* as its
    "already-initialised" sentinel: on each scan, if ``shortcuts.vdf``
    is **not** executable it overwrites the whole file with an empty
    ``{"shortcuts": {}}`` (NSLGameScanner.py) — wiping every shortcut,
    ours included. NSL itself always chmods the file to ``0o755`` after
    writing. Our tmp+``os.replace`` creates the destination inode at the
    process umask default (typically ``0o644`` — non-executable), which
    silently disarms that sentinel and makes the next scan erase the
    user's library ("0 games after sync"). Re-asserting ``0o755`` here —
    on the single lowest-level byte-writer of shortcuts.vdf, so every
    write path inherits it — matches the state Steam/NSL already leave
    and lets the two tools coexist. (Foreign NSL entries are separately
    preserved by :func:`merge_foreign_shortcuts`.)
    """
    def _write_sync() -> None:
        parent = str(Path(shortcuts_path).parent)
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)

        tmp_path = shortcuts_path + ".tmp"
        try:
            with Path(tmp_path).open("wb") as f:
                f.write(vdf.binary_dumps(data))  # type: ignore[no-untyped-call]
            Path(tmp_path).replace(shortcuts_path)
        except Exception:
            logger.exception("[ShortcutPersistence] failed to write shortcuts.vdf")
            if Path(tmp_path).exists():
                with contextlib.suppress(OSError):
                    Path(tmp_path).unlink()
            return

        # Preserve the executable bit NSL's scanner uses as its
        # "already-initialised" sentinel (see docstring). A chmod
        # failure (exotic filesystem) must not fail the write — the
        # bytes are already safely in place.
        try:
            was_exec = os.access(shortcuts_path, os.X_OK)
            # 0o755 is required, not permissive-by-accident: it is the
            # exact mode Steam/NSL keep and NSL's scanner requires (see
            # docstring). A stricter mode reintroduces the library wipe.
            os.chmod(shortcuts_path, 0o755)  # noqa: S103
            if not was_exec:
                logger.info(
                    "[ShortcutPersistence] restored executable bit on "
                    "shortcuts.vdf (0o755) — prevents NonSteamLaunchers' "
                    "scanner from wiping the library on its next pass",
                )
        except OSError as e:
            logger.warning(
                "[ShortcutPersistence] could not set executable bit on "
                "shortcuts.vdf: %s (NSL, if installed, may reset the file)",
                e,
            )

    await asyncio.to_thread(_write_sync)


def _shortcut_entries(data: dict[str, Any]) -> dict[str, Any]:
    """Return the inner ``shortcuts`` sub-dict of a loaded vdf (or ``{}``).

    ``shortcuts.vdf`` wraps entries under a top-level ``"shortcuts"``
    key; a third party can leave the file in a shape without it.
    """
    inner = data.get("shortcuts") if isinstance(data, dict) else None
    return inner if isinstance(inner, dict) else {}


def _merge_one_foreign(
    entry: Any,
    mem_inner: dict[str, Any],
    known_appids: set[Any],
    launcher_path: str,
) -> bool:
    """Merge a single disk *entry* back into *mem_inner* if it is a foreign
    shortcut memory lost. Returns ``True`` if it was merged.

    Extracted from :func:`merge_foreign_shortcuts` to keep that function
    under the cognitive-complexity cap; behaviour is identical. Mutates
    *mem_inner* / *known_appids* in place on a merge.
    """
    if not isinstance(entry, dict):
        return False
    exe_raw = entry.get("Exe") or entry.get("exe") or ""
    exe = exe_raw.strip().strip('"') if isinstance(exe_raw, str) else ""
    # Ours: memory is the source of truth (respect our own deletes).
    if _is_launcher_exe(exe, launcher_path):
        return False
    appid = entry.get("appid")
    # Foreign entry already represented in memory — leave memory's
    # copy (our writes never mutate foreign entries anyway).
    if appid is not None and appid in known_appids:
        return False
    new_key = str(len(mem_inner))
    while new_key in mem_inner:
        new_key = str(int(new_key) + 1)
    mem_inner[new_key] = entry
    if appid is not None:
        known_appids.add(appid)
    return True


def merge_foreign_shortcuts(
    mem: dict[str, Any], disk: dict[str, Any], launcher_path: str,
) -> int:
    """Re-inject foreign shortcuts that ``mem`` lost since it was loaded.

    Unifideck holds ``self._shortcuts`` in memory for the lifetime of
    the service and writes the whole dict back on every ``_save_all``.
    A concurrent writer — NonSteamLaunchers' scanner service, Steam's
    own shutdown flush, a manual add — can append entries to the
    on-disk file *after* our snapshot; without this merge the next
    ``_save_all`` overwrites them (a lost update). UD-006's Exe-gate
    stopped reconcile from *deleting* foreign shortcuts, but not this
    stale-snapshot *overwrite*, which is the residual UD-043 data loss.

    Ownership is decided the same way reconcile decides it: an entry
    whose ``Exe`` basename is our ``unifideck-launcher``
    (:func:`orphan_scan._is_launcher_exe`) is *ours* — memory is
    authoritative for it, so an entry we deliberately dropped stays
    dropped. Every other entry is *foreign*: if it is present on disk
    but absent from memory (matched by ``appid``), it is copied back
    into ``mem`` under a fresh non-colliding key. Foreign entries the
    user or Steam removed on disk are honoured too — we only *add*
    what disk has and memory lost, never resurrect our own deletions.

    Mutates ``mem`` in place and returns the number of entries merged
    back (0 in the common no-conflict case, so callers can skip the
    write-back log when nothing changed).
    """
    disk_inner = _shortcut_entries(disk)
    if not disk_inner:
        return 0
    # Ensure the wrapper exists so mutations land on the object the
    # caller will write back (``mem["shortcuts"]``), not a throwaway.
    if not isinstance(mem.get("shortcuts"), dict):
        mem["shortcuts"] = {}
    mem_inner = mem["shortcuts"]

    known_appids = {
        e.get("appid") for e in mem_inner.values()
        if isinstance(e, dict) and e.get("appid") is not None
    }
    merged = 0
    for entry in disk_inner.values():
        if _merge_one_foreign(entry, mem_inner, known_appids, launcher_path):
            merged += 1

    if merged:
        logger.info(
            "[ShortcutPersistence] merged %d foreign shortcut(s) that a "
            "concurrent writer added since load — preventing a lost update",
            merged,
        )
    return merged


async def read_games_map(games_map_path: str) -> dict[str, GameMapEntry]:
    """Load games.map with retry-on-corruption.

    Up to ``_GAMES_MAP_READ_ATTEMPTS`` attempts spaced
    ``_GAMES_MAP_RETRY_DELAY_S`` apart — a concurrent
    ``save_all`` can leave the file briefly partial between
    the truncate and the final flush. Transient errors
    (OSError rename race, UnicodeDecodeError mid-write)
    all retry. Returns ``{}`` on missing file or
    irrecoverable malformation.
    """
    if not await asyncio.to_thread(lambda: Path(games_map_path).is_file()):
        return {}

    for attempt in range(1, _GAMES_MAP_READ_ATTEMPTS + 1):
        try:
            def _read_sync() -> str:
                with Path(games_map_path).open(encoding="utf-8") as f:
                    return f.read()

            content = await asyncio.to_thread(_read_sync)
            return parse_games_map(content)
        except Exception as e:
            if attempt < _GAMES_MAP_READ_ATTEMPTS:
                logger.debug(
                    "[ShortcutPersistence] games.map read failed (attempt %d/%d): %s. Retrying...",
                    attempt, _GAMES_MAP_READ_ATTEMPTS, e,
                )
                await asyncio.sleep(_GAMES_MAP_RETRY_DELAY_S)
            else:
                logger.warning(
                    "[ShortcutPersistence] games.map read failed permanently after %d attempts: %s",
                    _GAMES_MAP_READ_ATTEMPTS, e,
                )

    return {}


async def write_games_map(games_map_path: str, games_map: dict[str, GameMapEntry]) -> None:
    """Persist games.map atomically.

    Uses the POSIX ``tmpfile + os.replace`` pattern: write content to
    ``<path>.tmp``, then rename. Readers mid-read see either
    old or new content, never a half-written file — eliminates
    the race where the launcher dispatcher reads between our
    truncate and the subsequent writes.
    """
    def _write_sync() -> None:
        parent = str(Path(games_map_path).parent)
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)

        content = format_games_map(games_map)
        tmp_path = games_map_path + ".tmp"

        try:
            with Path(tmp_path).open("w", encoding="utf-8") as f:
                f.write(content)
                # Ensure it's fully written to disk before rename
                f.flush()
                os.fsync(f.fileno())

            Path(tmp_path).replace(games_map_path)
        except Exception:
            logger.exception("[ShortcutPersistence] failed to write games.map")
            if Path(tmp_path).exists():
                with contextlib.suppress(OSError):
                    Path(tmp_path).unlink()

    await asyncio.to_thread(_write_sync)
