"""services/shortcut/reconcile_helpers.py — pure helpers for reconcile.

Stateless module helpers split out of ``reconcile_phases.py`` (which had
crossed the 550-LOC volumetry cap): the one-time migration-marker touch,
the duplicate-shortcut sweep, the "restart Steam" banner, and the
launch-options index. ``_ReconcilePhasesMixin`` imports these.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .launch_options import get_full_id

logger = logging.getLogger(__name__)


def touch_marker(marker: Path) -> None:
    """Create a one-time migration marker file (best-effort)."""
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("done", encoding="utf-8")
    except OSError as e:
        logger.warning(
            "[ShortcutService] could not write migration marker %s: %s",
            marker, e,
        )


def dedup_shortcuts(shortcuts_dict: dict[str, Any]) -> int:
    """Drop duplicate VDF entries sharing launch-options; return the count.

    Scores each group by metadata richness and keeps the winner.
    """
    from .dedup import find_duplicate_losers
    losers = find_duplicate_losers(shortcuts_dict)
    for loser_key in losers:
        shortcuts_dict.pop(loser_key, None)
    return len(losers)


def log_restart_banner(added: int, removed: int, reclaimed: int) -> None:
    """Log the "restart Steam to see changes" banner for tailed logs."""
    for line in (
        "=" * 60,
        "IMPORTANT: Steam restart required to see shortcut changes!",
        f"  (added={added} removed={removed} reclaimed={reclaimed})",
        ("Please EXIT Steam completely and restart for the "
         "shortcuts.vdf changes to take effect."),
        "=" * 60,
    ):
        logger.warning("[ShortcutService] %s", line)


def build_launch_index(shortcuts_dict: dict[str, Any]) -> dict[str, str]:
    """Map ``"store:game_id"`` → VDF ordinal key for every shortcut.

    One O(N) pass over ``shortcuts_dict`` so per-game lookups in
    ``_sync_one_game`` are O(1). Entries with missing or
    non-string ``LaunchOptions`` and entries whose
    ``LaunchOptions`` doesn't parse as Unifideck form are
    skipped silently.
    """
    launch_to_key: dict[str, str] = {}
    for vdf_key, entry in shortcuts_dict.items():
        if not isinstance(entry, dict):
            continue
        launch = entry.get("LaunchOptions", "")
        if not isinstance(launch, str) or not launch:
            continue
        full_id = get_full_id(launch)
        if full_id:
            launch_to_key[full_id] = vdf_key
    return launch_to_key
