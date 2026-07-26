"""core/stale_installs.py — Clear a game's stale install state before install.

WHY THIS EXISTS
---------------
The store CLIs keep their own "what is installed" record, and that record can
outlive the files it points at — a manual delete, a moved SD card, a failed
"Delete all data", a partial uninstall. When it does, the CLI believes the
game is already installed and an install request becomes a **no-op**.

Field report (Amazon, "The Gap"): the install returned in 1.4 s having
downloaded nothing::

    executing: nile install amzn1.adg.product.5d4cab76… --base-path ~/Games
    cannot locate install directory … nile reported success but no
    matching directory found on disk
    failed install for amazon:amzn1.adg.product.5d4cab76…: install_dir_not_found

``~/.config/nile/installed.json`` listed FOUR installed games and not one of
their directories existed. nile saw its own entry, concluded there was
nothing to do, exited 0, and the install could never succeed no matter how
many times the user retried. Only a hand-edit of nile's state file, or
reinstalling to a different path, would break the loop.

``amazon_library.py`` already guards the *display* side of this ("nile's
installed.json can outlive the directory"), so a stale entry does not show a
false PLAY button — but nothing reconciled the record before an install.

SCOPE — deliberately global
---------------------------
Called from the one seam every store install passes through
(``services/download/worker._dispatch_install``) rather than from the Amazon
adapter, because the failure mode is not Amazon's: any store whose CLI keeps
an install record can strand a game the same way. legendary keeps one too.
GOG and Ubisoft have no equivalent CLI record, so for them only the
leftover-directory sweep applies — which is still worth doing, since a stub
directory left by a half-finished install is what makes the *next* install
land somewhere unexpected.

SAFETY
------
Every rule here exists to make this incapable of destroying a real install:

* A record is pruned ONLY when the path it names is missing from disk. A
  record whose files exist is never touched, so a healthy install is
  untouchable even if this is called by mistake.
* Never run for an UPDATE — an update legitimately expects existing files.
  The caller enforces that.
* Directory removal is delegated to :mod:`marker_sweep`, which only ever
  deletes directories carrying OUR ownership marker.
* Best-effort throughout: cleanup failure logs and returns, it never blocks
  the install. Being unable to tidy up is not a reason to refuse to try.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# store -> (record file, path field). The record's SHAPE differs per CLI and
# is handled in the readers below: nile writes a LIST of entries carrying an
# ``id``; legendary writes a DICT keyed by the game id.
_NILE_RECORD = "~/.config/nile/installed.json"
_LEGENDARY_RECORD = "~/.config/legendary/installed.json"


def _load(path: str) -> Any | None:
    """Parse a CLI record file, or ``None`` if absent/unreadable/corrupt."""
    resolved = Path(path).expanduser()
    try:
        if not resolved.is_file():
            return None
        with resolved.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("[stale_installs] cannot read %s: %s", resolved, e)
        return None


def _write_atomic(path: str, data: Any) -> bool:
    """Replace a CLI record file atomically. True on success.

    Temp file in the same directory + ``os.replace`` so a crash mid-write
    cannot leave the CLI with a truncated state file — losing a store's
    entire install record would be a far worse bug than the one being fixed.
    """
    resolved = Path(path).expanduser()
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(resolved.parent), prefix=resolved.name, suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, resolved)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as e:
        logger.warning("[stale_installs] cannot rewrite %s: %s", resolved, e)
        return False
    return True


def _path_is_missing(recorded: str | None) -> bool:
    """Whether a recorded install path is absent from disk.

    An empty/absent path counts as missing: the record claims an install
    with nowhere to point, which is exactly as unusable as a dangling one.
    """
    if not recorded:
        return True
    return not Path(recorded).expanduser().is_dir()


def _prune_nile(game_id: str) -> str | None:
    """Drop a stale ``nile`` entry (list shape, ``id``/``path``)."""
    data = _load(_NILE_RECORD)
    if not isinstance(data, list):
        return None
    keep = []
    dropped: str | None = None
    for entry in data:
        if not isinstance(entry, dict):
            keep.append(entry)
            continue
        if entry.get("id") != game_id:
            keep.append(entry)
            continue
        recorded = entry.get("path")
        if not _path_is_missing(recorded):
            # Files are there — this is a real install, leave it alone.
            return None
        dropped = str(recorded or "<no path>")
    if dropped is None or not _write_atomic(_NILE_RECORD, keep):
        return None
    return f"nile installed.json entry (path was {dropped})"


def _prune_legendary(game_id: str) -> str | None:
    """Drop a stale ``legendary`` entry (dict shape, ``install_path``)."""
    data = _load(_LEGENDARY_RECORD)
    if not isinstance(data, dict) or game_id not in data:
        return None
    entry = data[game_id]
    recorded = entry.get("install_path") if isinstance(entry, dict) else None
    if not _path_is_missing(recorded):
        return None
    del data[game_id]
    if not _write_atomic(_LEGENDARY_RECORD, data):
        return None
    return f"legendary installed.json entry (path was {recorded or '<no path>'})"


# Only these stores keep a CLI-side install record that can veto an install.
_PRUNERS = {
    "amazon": _prune_nile,
    "epic": _prune_legendary,
}


def reconcile_for_install(store: str, game_id: str) -> list[str]:
    """Clear stale local state for ``(store, game_id)`` before installing.

    Returns a human-readable list of what was cleaned, empty when there was
    nothing to do (the overwhelmingly common case — this is a couple of
    ``stat`` calls on a healthy system).

    NEVER call this for an update. See the module docstring.
    """
    cleaned: list[str] = []

    pruner = _PRUNERS.get(store)
    if pruner is not None:
        try:
            note = pruner(game_id)
        except Exception:
            logger.exception(
                "[stale_installs] pruning %s record for %s failed (non-fatal)",
                store, game_id,
            )
        else:
            if note:
                cleaned.append(note)

    # A leftover directory carrying our marker means a previous install got
    # part-way and left a stub. marker_sweep only ever removes directories we
    # created, so this cannot touch a user's own folder.
    try:
        from unifideck.core import marker_sweep

        roots = marker_sweep.collect_install_roots()
        target = marker_sweep.find_for_game(roots, store, game_id)
        if target is not None and marker_sweep.sweep_game(store, game_id):
            cleaned.append(f"leftover install dir {target}")
    except Exception:
        logger.exception(
            "[stale_installs] marker sweep for %s:%s failed (non-fatal)",
            store, game_id,
        )

    if cleaned:
        logger.info(
            "[stale_installs] cleared stale state for %s:%s — %s",
            store, game_id, "; ".join(cleaned),
        )
    return cleaned
