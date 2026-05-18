"""Metadata-aware shortcut deduplication.

Steam occasionally creates duplicate ``shortcuts.vdf`` entries with
the same launch options — typically when its in-memory state desyncs
from disk (e.g. after a crash). Without dedup, those duplicates
accrete over time and the user ends up with two tiles for the same
game, with playtime / artwork / tags split unpredictably between
them.

This module groups shortcuts by their canonical launch-options key,
scores each by metadata richness, and returns the indices of the
losers so the caller can drop them. The "richest" entry wins so we
keep the one with playtime + artwork + tags rather than the bare
clone Steam just made.

Scoring weights mirror staging's ``_deduplicate_shortcuts_data``:

* ``LastPlayTime`` (2)         — irreplaceable history.
* icon set (1)                 — user / SGDB artwork.
* ``Playtime_Forever`` (1)     — recorded play time.
* rich tag set (1)             — store + category tags.
* exe path quality (2)         — looks like a real path.
* ``StartDir`` set (1).
* ``AppName`` populated (1).
* exe basename matches name (1).

Higher score wins; ties resolve by preserving the lower index (first
seen — typically the original entry rather than the duplicate).
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_launch_options(value: Any) -> str:
    """Strip the per-user-params suffix so duplicates compare equal.

    Launch-options look like ``"epic:1234"`` or
    ``"epic:1234 [extra_user_param=1]"`` — the bracketed user-param
    section is informational and shouldn't break the dedup grouping.
    """
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s*\[.*?\]\s*$", "", value).strip()


def _score_one(entry: dict[str, Any]) -> int:
    """Score a single VDF shortcut entry by metadata richness."""
    score = 0
    if entry.get("LastPlayTime"):
        score += 2
    icon = entry.get("icon") or entry.get("Icon")
    if isinstance(icon, str) and icon:
        score += 1
    if entry.get("Playtime_Forever") or entry.get("playtime_forever"):
        score += 1
    tags = entry.get("tags") or entry.get("Tags") or {}
    if isinstance(tags, dict) and any(v for v in tags.values()):
        score += 1
    exe = entry.get("exe") or entry.get("Exe") or ""
    if isinstance(exe, str) and exe and exe not in ("/", ""):
        score += 2
    if entry.get("StartDir") or entry.get("startdir"):
        score += 1
    appname = entry.get("AppName") or entry.get("appname") or ""
    if isinstance(appname, str) and appname.strip():
        score += 1
    if (
        isinstance(exe, str)
        and isinstance(appname, str)
        and exe
        and appname
        and appname.lower() in exe.lower()
    ):
        score += 1
    return score


def find_duplicate_losers(
    shortcuts_dict: dict[str, Any],
) -> list[str]:
    """Group by launch-options and return the keys of the losers.

    Caller deletes the returned keys from ``shortcuts_dict`` to
    persist the winners only. Empty / missing launch-options group
    is treated as ungrouped (each entry is its own group, never
    deduped).

    Returns:
        List of dict keys to remove. Caller should ``pop`` them.
    """
    by_key: dict[str, list[tuple[str, int]]] = {}
    for k, entry in shortcuts_dict.items():
        if not isinstance(entry, dict):
            continue
        canonical = _normalize_launch_options(
            entry.get("LaunchOptions") or entry.get("launchoptions"),
        )
        if not canonical:
            continue
        by_key.setdefault(canonical, []).append((k, _score_one(entry)))
    losers: list[str] = []
    for canonical, candidates in by_key.items():
        if len(candidates) < 2:
            continue
        # Highest score wins; tie → lowest dict-key (first inserted).
        candidates.sort(key=lambda pair: (-pair[1], pair[0]))
        winner = candidates[0]
        for loser_key, loser_score in candidates[1:]:
            losers.append(loser_key)
            logger.info(
                "[ShortcutService.dedup] drop duplicate %r "
                "(score=%d) — winner %r (score=%d)",
                canonical, loser_score, winner[0], winner[1],
            )
    return losers
