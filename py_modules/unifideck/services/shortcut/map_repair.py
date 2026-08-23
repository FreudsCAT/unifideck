"""services/shortcut/map_repair.py — rebuild games.map rows that went missing.

Reconcile maintains ``games.map`` from the synced library, but a
library-sourced ``Game`` only carries ``exe_path`` for the stores that
scan the disk themselves (GOG, Ubisoft). Epic and Amazon resolve it at
install time instead, so ``_update_games_map_row`` sees ``installed=True``
with an empty exe and — deliberately — leaves the existing row alone
rather than wiping it. When there is no row to leave alone, nothing ever
creates one.

That is not a corner case: it is every situation where the games outlive
the plugin's data dir. "Delete plugin data" with the games kept, a
reinstall onto a machine whose library is already on disk, a partial
restore, a move to another Deck.

The failure is silent, which is what makes it expensive. The library
looks healthy and the games still launch, because the dispatcher
re-resolves the executable by search (``_resolve_exe_from_install``).
What it cannot invent is the row's ``app_id``: without it
``_game_context`` leaves ``ctx.steam_app_id`` at ``None``,
``select_proton_version`` skips its first tier, and the user's
Properties > Compatibility choice is ignored across the whole library —
no error, no warning, not even the ``steam force-compat lookup`` line
that would say the tier was consulted.

This module runs after reconcile's phases and fills those rows back in,
reusing the same ``exe_finder`` the launcher already falls back to. It
lives outside ``reconcile_phases.py`` for the reason
``lastplaytime_reset`` does: that file sits against the 550-LOC
volumetry cap.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .games_map import GameMapEntry, generate_app_id
from .reconcile_helpers import build_launch_index

if TYPE_CHECKING:
    from unifideck.core.types import Game

logger = logging.getLogger(__name__)


def _shortcut_app_id(
    shortcuts_dict: dict[str, Any], launch_index: dict[str, str], key: str,
) -> int | None:
    """The appid Steam already holds for *key*'s shortcut, or ``None``.

    Preferred over recomputing, for the reason ``mark_installed``
    documents: the shortcut's own appid is authoritative. Regenerating
    it diverges whenever ``game.app_id`` carries a store-side value, and
    every appid-keyed lookup downstream — artwork, playtime, and the
    ``CompatToolMapping`` entry this whole repair exists to reach —
    then points at a shortcut that does not exist.
    """
    ord_key = launch_index.get(key)
    if ord_key is None:
        return None
    entry = shortcuts_dict.get(ord_key)
    if not isinstance(entry, dict):
        return None
    app_id = entry.get("appid")
    return app_id if isinstance(app_id, int) else None


def _collect_missing(
    games: list[Game],
    games_map: dict[str, GameMapEntry],
    shortcuts_dict: dict[str, Any],
    launch_index: dict[str, str],
    launcher: str,
) -> list[tuple[str, int, str]]:
    """``(key, app_id, install_path)`` for each installed game with no row.

    Pure and cheap — no filesystem access — so the expensive part
    (walking install dirs) only runs for the games that actually need
    it, which on a healthy library is none of them.
    """
    missing: list[tuple[str, int, str]] = []
    for game in games:
        key = f"{game.store}:{game.store_game_id}"
        install_path = game.install_path or ""
        if not game.installed or not install_path or key in games_map:
            continue
        app_id = (
            _shortcut_app_id(shortcuts_dict, launch_index, key)
            or game.app_id
            or generate_app_id(launcher, key)
        )
        missing.append((key, app_id, install_path))
    return missing


def _resolve_exe(install_path: str) -> str | None:
    """Best-effort launch target for *install_path*. Runs off-thread.

    Mirrors ``launcher.dispatcher._resolve_exe_from_install``, including
    the ``start.sh`` check: native Linux games (notably GOG) launch
    through a wrapper script at the install root, which ``exe_finder``
    — a ``.exe`` picker — never returns.

    Two distinct empty results, and the caller treats them differently:

    * ``None`` — the directory is gone. A stale manifest entry, not a
      repairable row; writing one would resurrect a dead game.
    * ``""`` — the directory is there but nothing launchable scored.
      Still worth a row: its payload here is the ``app_id``, and for a
      Ubisoft title the row's mere existence is the "installed" signal
      that routes Play to the ``uplay://`` deeplink instead of
      reopening Ubisoft Connect.
    """
    from unifideck.core.exe_finder import exe_finder

    if not Path(install_path).is_dir():
        return None
    if (start_sh := Path(install_path) / "start.sh").is_file():
        return str(start_sh)
    try:
        return exe_finder.find(install_path) or ""
    except OSError:
        logger.exception("[map_repair] exe scan failed under %s", install_path)
        return ""


def _log_repaired_row(key: str, app_id: int, exe: str, install_path: str) -> None:
    """Record one repaired row; a row with no exe earns its own warning."""
    if exe:
        logger.info("[map_repair] %s → app_id=%d exe=%s", key, app_id, exe)
        return
    logger.warning(
        "[map_repair] %s → app_id=%d, but nothing launchable found under "
        "%s. Row written anyway: the app_id restores the Steam "
        "force-compat lookup even when the exe has to be re-resolved at "
        "launch time",
        key, app_id, install_path,
    )


async def repair_missing_rows(svc: Any, games: list[Game]) -> int:
    """Write a games.map row for every installed game that has none.

    Called by ``reconcile`` after its phases have settled, so it sees
    the final state of both ``_games_map`` (phase 1 has pruned, phase 2
    has written) and ``shortcuts.vdf`` (a shortcut added this very sync
    is already indexable, and its appid is the one we want).

    Returns how many rows were written. The caller folds that into the
    reconcile tally and — load-bearing — into the decision to persist:
    ``reconcile`` only calls ``_save_all`` when something changed, and
    an otherwise-stable library produces nothing but ``kept``, so
    without this count the repaired rows would live and die in memory.
    """
    launcher = getattr(svc, "_launcher_path", "") or ""
    root = svc._shortcuts.get("shortcuts") if isinstance(svc._shortcuts, dict) else None
    shortcuts_dict = root if isinstance(root, dict) else {}
    missing = _collect_missing(
        games, svc._games_map, shortcuts_dict,
        build_launch_index(shortcuts_dict, launcher), launcher,
    )
    if not missing:
        return 0

    logger.info(
        "[map_repair] %d installed game(s) have no games.map row — "
        "re-resolving their executables", len(missing),
    )
    repaired = 0
    for key, app_id, install_path in missing:
        exe = await asyncio.to_thread(_resolve_exe, install_path)
        if exe is None:
            logger.warning(
                "[map_repair] %s: install dir %s no longer exists, "
                "leaving it to the next sync's prune", key, install_path,
            )
            continue
        svc._games_map[key] = GameMapEntry(
            exe=exe, work_dir=install_path, app_id=app_id,
        )
        _log_repaired_row(key, app_id, exe, install_path)
        repaired += 1
    return repaired
