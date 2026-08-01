"""Find stale ``compatdata`` prefixes left behind by older launch paths.

py_modules/unifideck/services/shortcut/compatdata_scan.py

Before the ``compatdata`` bridge existed, launching a Unifideck shortcut with
a compat tool assigned made **Steam** create a full Proton prefix at
``steamapps/compatdata/<appid>`` — 300–800 MB each. Those prefixes are dead
weight: the launcher sets ``WINEPREFIX`` to our own per-game directory, so
the game never reads them. Nothing pruned them on uninstall either, so they
accumulate, and Protontricks lists them *instead of* the real prefix — the
user edits a prefix the game does not use.

Every entry is classified into exactly one of three buckets:

``unifideck``  the appid maps to a shortcut tagged as ours → safe to delete;
``orphan``     no shortcut with that appid exists at all → safe to delete;
``user``       someone else's non-Steam shortcut → **never offered, never
               deleted**. This bucket is the whole point of the scan: a Deck
               in testing had two real, in-use prefixes (1.0 GB) sitting in
               the same appid range as ours.

Bridge symlinks created by ``core.compat_bridge`` are skipped outright — they
are not directories on disk and must never be reported as reclaimable.

Read-only. Deletion is the caller's job (``rpc/mixins/sync_cleanup``), gated
on an explicit user confirmation.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from unifideck.core.compat_bridge import compatdata_dir, to_unsigned

from .games_map import UNIFIDECK_TAG

logger = logging.getLogger(__name__)

#: Steam gives non-Steam shortcuts appids above 2^31; real Steam appids are
#: far below it. Scanning only above this bound keeps the sweep away from
#: genuine Steam game prefixes entirely.
NONSTEAM_APPID_MIN = 2_000_000_000

CLASS_UNIFIDECK = "unifideck"
CLASS_ORPHAN = "orphan"
CLASS_USER = "user"

#: Buckets the caller may delete. ``CLASS_USER`` is deliberately absent.
DELETABLE = (CLASS_UNIFIDECK, CLASS_ORPHAN)


def _is_ours(entry: dict[str, Any]) -> bool:
    """True iff a ``shortcuts.vdf`` entry is a Unifideck-managed shortcut."""
    tags = entry.get("tags")
    tagvals = list(tags.values()) if isinstance(tags, dict) else []
    if UNIFIDECK_TAG in tagvals:
        return True
    return "unifideck-launcher" in str(entry.get("exe", ""))


def index_shortcuts(shortcuts: dict[str, Any]) -> dict[int, tuple[str, bool]]:
    """``{u32 appid: (name, is_unifideck)}`` from a parsed shortcuts dict.

    Accepts the ``{"0": {...}, "1": {...}}`` mapping that lives under the
    ``shortcuts`` key of a parsed ``shortcuts.vdf``.
    """
    index: dict[int, tuple[str, bool]] = {}
    for raw in shortcuts.values():
        if not isinstance(raw, dict):
            continue
        entry = {str(k).lower(): v for k, v in raw.items()}
        app_id = entry.get("appid")
        if app_id is None:
            continue
        try:
            key = to_unsigned(app_id)
        except (TypeError, ValueError):
            continue
        index[key] = (str(entry.get("appname", "")), _is_ours(entry))
    return index


def _dir_size_bytes(path: Path) -> int:
    """Recursive size of *path*; unreadable entries count as 0."""
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file() and not child.is_symlink():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        logger.debug("[compatdata_scan] could not walk %s", path)
    return total


def classify(app_id: int, index: dict[int, tuple[str, bool]]) -> tuple[str, str]:
    """``(classification, display name)`` for *app_id*."""
    hit = index.get(app_id)
    if hit is None:
        return CLASS_ORPHAN, ""
    name, ours = hit
    return (CLASS_UNIFIDECK if ours else CLASS_USER), name


def scan(
    steam_root: Path | str | None,
    shortcuts: dict[str, Any] | None,
    *,
    with_sizes: bool = True,
) -> dict[str, Any]:
    """Classify every non-Steam ``compatdata`` directory under *steam_root*.

    Returns ``{"entries": [...], "deletable_bytes": int, "deletable_count":
    int}``, where each entry carries ``app_id``, ``name``, ``classification``,
    ``path``, ``size_bytes`` and ``deletable``. Never raises.
    """
    empty: dict[str, Any] = {
        "entries": [], "deletable_bytes": 0, "deletable_count": 0,
    }
    if not steam_root:
        return empty
    root = compatdata_dir(Path(steam_root).expanduser())
    if not root.is_dir():
        return empty

    index = index_shortcuts(shortcuts or {})
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        logger.exception("[compatdata_scan] cannot list %s", root)
        return empty

    for child in children:
        # Our own bridge links are symlinks, not real prefixes — skip them so
        # a live game's prefix can never be offered up for deletion.
        if child.is_symlink() or not child.name.isdigit():
            continue
        app_id = int(child.name)
        if app_id < NONSTEAM_APPID_MIN or not child.is_dir():
            continue
        classification, name = classify(app_id, index)
        entries.append({
            "app_id": app_id,
            "name": name,
            "classification": classification,
            "path": str(child),
            "size_bytes": _dir_size_bytes(child) if with_sizes else 0,
            "deletable": classification in DELETABLE,
        })

    deletable = [e for e in entries if e["deletable"]]
    return {
        "entries": entries,
        "deletable_bytes": sum(e["size_bytes"] for e in deletable),
        "deletable_count": len(deletable),
    }
