"""
Steam cross-reference filter — hide Ubisoft games already in the Steam library.

OP-55i | py_modules/unifideck/stores/ubisoft/steam_filter.py

When the user owns the same game on both Steam and Ubisoft Connect
(common with Assassin's Creed, Far Cry, Rainbow Six titles), we don't
want to display it twice in the unified library — the Steam version
should win because launching it through Steam is more reliable than
through the UPC overlay.

This module exposes pure functions that, given the user's Steam library
and the Ubisoft owned-games list, return the filtered Ubisoft list with
known-Steam games removed. Filtering is controlled by
``UbisoftConfig.filter_steam_linked`` (on by default) and the optional
``steam_library_cross_ref`` toggle (off by default; enables fuzzy
cross-matching by name when the SteamGridDB id isn't available).
"""

from __future__ import annotations
import logging
from typing import Any
from .id_map import UbisoftIdMap

logger = logging.getLogger(__name__)
_STEAM_YAML_MARKERS = (
    "steam_installer:",
    "steam_app_id:",
    "valve\\\\steam",
    "valve\\steam",
)


def filter_steam_linked_configs(
    configs: list[Any],
    steam_library_cross_ref_enabled: bool,
    id_map: UbisoftIdMap,
) -> list[Any]:
    """Filter steam linked configs."""
    steam_titles = load_steam_titles_for_cross_ref(
        steam_library_cross_ref_enabled,
    )
    kept: list[Any] = []
    for cfg in configs:
        drop_reason = classify_steam_linked(cfg, steam_titles, id_map)
        if drop_reason is None:
            kept.append(cfg)
            continue
        logger.debug(
            "[UbisoftLibrary] %s drop Steam-linked: %s",
            drop_reason,
            cfg.name,
        )
    return kept


def load_steam_titles_for_cross_ref(
    enabled: bool,
) -> set[str]:
    """Load steam titles for cross ref."""
    if not enabled:
        return set()
    steam_titles = UbisoftIdMap.get_steam_library_titles()
    if steam_titles:
        logger.debug(
            "[UbisoftLibrary] Steam library cross-ref enabled with %d titles",
            len(steam_titles),
        )
    return steam_titles


def classify_steam_linked(
    cfg: Any,
    steam_titles: set[str],
    id_map: UbisoftIdMap,
) -> str | None:
    """Classify steam linked."""
    tp_platform = (getattr(cfg, "third_party_platform", "") or "").lower()
    if tp_platform and "steam" in tp_platform:
        return "L1"
    yaml_raw = getattr(cfg, "yaml_raw", "") or ""
    if yaml_has_steam_markers(yaml_raw):
        return "L2"
    if steam_titles:
        norm = id_map.normalize_for_matching(cfg.name or "")
        if norm and norm in steam_titles:
            return "L3"
    return None


def yaml_has_steam_markers(yaml_raw: str) -> bool:
    """Yaml has steam markers."""
    if not yaml_raw:
        return False
    lowered = yaml_raw.lower()
    return any(marker in lowered for marker in _STEAM_YAML_MARKERS)
