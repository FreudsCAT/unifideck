"""Keep ``compatdata`` bridge links in sync with the installed games.

py_modules/unifideck/services/prefix_bridge.py

One sweep that makes external Wine tooling (Protontricks) agree with
reality. It is driven by ``games.map``, which lists exactly the games that
are *installed* and carries the canonical Steam ``app_id`` in its v3 column —
so "installed games only" falls out of the data source rather than needing a
separate install-state query. Recomputing the appid here would be wrong:
``generate_app_id`` is anchored on the launcher **exe path**, so a derived id
does not match the stored one (verified on-device).

Three actions, all idempotent:

1. link every installed game's prefix into ``steamapps/compatdata/<appid>``;
2. prune bridge links whose prefix is gone — this is what makes an uninstall
   (or a manual prefix deletion, or a half-failed uninstall) drop out of
   Protontricks, with no hook needed in any per-store uninstall path;
3. ensure the Protontricks Flatpak can actually read the prefixes dir.

Running it on a schedule rather than wiring each store's uninstall keeps the
logic in one place and driven by ground truth (does the prefix exist?),
instead of five call sites that can each forget.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from unifideck.core import compat_bridge
from unifideck.services.shortcut.games_map import parse_games_map

logger = logging.getLogger(__name__)

_DATA_DIR = Path("~/.local/share/unifideck").expanduser()
_GAMES_MAP = _DATA_DIR / "games.map"
_UBISOFT_ID_MAP = _DATA_DIR / "ubisoft_id_map.json"


def _ubisoft_prefix(game_id: str) -> Path:
    """Ubisoft's per-game prefix — mirrors the launcher's ``_resolve_prefix``.

    Ubisoft games can be installed to a user-picked location, so the absolute
    prefix path is recorded in ``ubisoft_id_map.json``; the namespaced default
    is the fallback for games installed before that existed.
    """
    import json

    try:
        data = json.loads(_UBISOFT_ID_MAP.read_text(encoding="utf-8"))
        entry = data.get(game_id) if isinstance(data, dict) else None
        recorded = entry.get("prefix_path") if isinstance(entry, dict) else None
        if recorded:
            return Path(recorded).expanduser()
    except (OSError, ValueError):
        pass
    return compat_bridge.PREFIX_ROOT / "ubisoft" / game_id


def _battlenet_prefix(game_id: str) -> Path:
    """Per-game Battle.net prefix, read from the id map — never rebuilt."""
    try:
        from unifideck.launcher.proton.handlers.battlenet_client import resolve_prefix as _rp

        recorded = _rp(game_id)
        if recorded is not None:
            return recorded
    except (OSError, ValueError, ImportError):
        pass
    return _DATA_DIR / "prefixes" / "battlenet" / game_id


def resolve_prefix(store: str, game_id: str) -> Path:
    """Prefix path for *store*/*game_id*, matching the launcher's resolution."""
    if store == "battlenet":
        return _battlenet_prefix(game_id)
    if store == "ubisoft":
        return _ubisoft_prefix(game_id)
    return compat_bridge.PREFIX_ROOT / game_id


def _installed_rows() -> list[tuple[str, str, int]]:
    """``(store, game_id, app_id)`` for every games.map row with a real appid."""
    try:
        content = _GAMES_MAP.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[tuple[str, str, int]] = []
    for key, entry in parse_games_map(content).items():
        store, _, game_id = key.partition(":")
        # app_id 0 is the "not yet backfilled" marker — skip rather than
        # bridge a bogus appid.
        if store and game_id and entry.app_id:
            rows.append((store, game_id, entry.app_id))
    return rows


def _live_prefixes() -> set[Path]:
    """Resolved prefixes of every installed game, for the last-ditch guard."""
    live: set[Path] = set()
    for store, game_id, _app_id in _installed_rows():
        try:
            live.add(resolve_prefix(store, game_id).resolve())
        except OSError:
            continue
    return live


def _is_live_prefix(path: str, live: set[Path]) -> bool:
    """True iff *path* is (or cannot be proven not to be) a live game prefix.

    Normally impossible — ours live under ``PREFIX_ROOT``, not ``compatdata`` —
    but a user-configured prefix root could overlap and deletion is
    irreversible. An unresolvable path counts as live (fail closed).
    """
    try:
        resolved = Path(path).resolve()
    except OSError:
        return True
    if resolved in live:
        logger.info("[prefix_bridge] %s is a live game prefix, keeping it", path)
        return True
    return False


def reclaim_redundant_compatdata(
    steam_root: Path | str | None, shortcuts: dict[str, Any] | None,
) -> dict[str, Any]:
    """Delete Steam-made ``compatdata`` prefixes that Unifideck initialised.

    Steam creates a full Proton prefix at ``steamapps/compatdata/<appid>``
    (300-800 MB) whenever a shortcut launches with a compat tool assigned, and
    nothing prunes it on uninstall. The launcher points ``WINEPREFIX`` at our
    own per-game directory, so that copy is dead weight, and Protontricks used
    to list it *instead of* the real prefix.

    Authorisation comes from a marker file **inside** the directory, never from
    the appid — see ``services/shortcut/compatdata_scan``. Three guards, all of
    which must pass:

    1. the directory carries a ``.unifideck*`` marker we wrote;
    2. its appid is not a *user*-owned shortcut, and its ``pfx.lock`` is not
       currently held (both in ``compatdata_scan.scan``);
    3. it is not the live prefix of an installed game (checked here, because
       only this module resolves prefixes from ``games.map``).

    Never raises: runs at boot and must not be able to break it.
    """
    from unifideck.core.safe_delete import safe_rmtree
    from unifideck.services.shortcut import compatdata_scan

    tally: dict[str, Any] = {
        "deleted": 0, "freed_bytes": 0, "kept": 0, "skipped_in_use": 0,
    }
    if not steam_root:
        return tally

    result = compatdata_scan.scan(steam_root, shortcuts or {})
    live = _live_prefixes()

    for entry in result["entries"]:
        if entry.get("in_use"):
            tally["skipped_in_use"] += 1
            continue
        if not entry["deletable"] or _is_live_prefix(entry["path"], live):
            tally["kept"] += 1
            continue
        path = Path(entry["path"])
        if safe_rmtree(path):
            logger.info(
                "[prefix_bridge] reclaimed %s (%.0f MB, proof=%s, class=%s)",
                path, entry["size_bytes"] / 2**20, entry["marker"],
                entry["classification"],
            )
            tally["deleted"] += 1
            tally["freed_bytes"] += entry["size_bytes"]
        else:
            logger.warning("[prefix_bridge] could not reclaim %s", path)

    if tally["deleted"]:
        logger.info(
            "[prefix_bridge] reclaimed %d stale prefixes, freed %.2f GB",
            tally["deleted"], tally["freed_bytes"] / 2**30,
        )
    return tally


def sync_bridges(steam_root: Path | str | None) -> dict[str, Any]:
    """Link installed prefixes, prune dead links, grant Flatpak access.

    Returns a small tally for logging. Never raises — this runs on boot and
    after every sync, and must not be able to break either.
    """
    result: dict[str, Any] = {
        "linked": 0, "already": 0, "pruned": 0, "failed": 0, "flatpak": "skipped",
        "tools_pruned": 0, "tools_flatpak": "skipped",
    }
    if not steam_root:
        logger.debug("[prefix_bridge] no steam root, skipping sweep")
        return result

    for store, game_id, app_id in _installed_rows():
        prefix = resolve_prefix(store, game_id)
        if not prefix.is_dir():
            continue
        action = compat_bridge.link_prefix(prefix, app_id, steam_root)
        if action == "noop":
            result["already"] += 1
        elif action in ("created", "repointed", "displaced"):
            result["linked"] += 1
        elif action == "failed":
            result["failed"] += 1

    result["pruned"] = compat_bridge.prune_dead_bridges(steam_root)

    try:
        from unifideck.services.protontricks_access import ensure_access

        result["flatpak"] = ensure_access()
    except Exception:  # optional tooling — never fatal
        logger.exception("[prefix_bridge] flatpak access check failed")
        result["flatpak"] = "failed"

    _sweep_compat_tool_bridges(result)

    logger.info(
        "[prefix_bridge] linked=%d already=%d pruned=%d failed=%d flatpak=%s "
        "tools_pruned=%d tools_flatpak=%s",
        result["linked"], result["already"], result["pruned"],
        result["failed"], result["flatpak"],
        result["tools_pruned"], result["tools_flatpak"],
    )
    return result


def _sweep_compat_tool_bridges(result: dict[str, Any]) -> None:
    """Keep the compat-tool bridge tidy and reachable from the sandbox.

    The links themselves are *created* at launch (``prefix_setup``), the only
    point where the Proton actually in use is known — the same division the
    prefix bridge uses. This sweep does the two things that must happen
    without a launch: drop links whose Proton was removed or upgraded in
    place, and (re)assert the Flatpak grant, which cannot be issued until the
    bridge directory exists.

    Mutates *result* in place; never raises. Protontricks is optional tooling.
    """
    try:
        from unifideck.core import compat_tool_bridge
        from unifideck.services.protontricks_access import (
            ensure_tool_path_access,
        )

        result["tools_pruned"] = compat_tool_bridge.prune_dead_links()
        result["tools_flatpak"] = ensure_tool_path_access()
    except Exception:  # optional tooling — never fatal
        logger.exception("[prefix_bridge] compat-tool bridge sweep failed")
        result["tools_flatpak"] = "failed"
