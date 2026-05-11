"""steam_filter.py — Drop UPC entries that are sold through Steam.

# OP-55i | py_modules/unifideck/stores/ubisoft/steam_filter.py | Depends: (none)

Some titles in the UPC ``configurations`` cache are Steam-linked
distributions (third-party platform = Steam). When the user already
owns them on Steam we don't want to surface them as a separate
Ubisoft entry. ``steam_library_cross_ref`` overrides this and is used
to *match* installed Steam titles against UPC entries instead.
"""
from __future__ import annotations

import logging
from typing import Any

from .id_map import UbisoftIdMap

logger = logging.getLogger(__name__)
_STEAM_YAML_MARKERS = (
    'steam_installer:', 'steam_app_id:', 'valve\\\\steam', 'valve\\steam',
)


def filter_steam_linked_configs(
    configs: list[Any],
    steam_library_cross_ref_enabled: bool,
    id_map: UbisoftIdMap,
) -> list[Any]:
    """Filter steam linked configs.

    With cross-ref **disabled** (default), drop any GameConfig whose
    YAML or third_party_platform indicates Steam — those titles are
    only really playable through Steam.

    With cross-ref **enabled**, keep them so callers can correlate
    space_id ↔ Steam appid for unified-library views.
    """
    if not configs:
        return []
    steam_titles = load_steam_titles_for_cross_ref(
        steam_library_cross_ref_enabled,
    )
    out: list[Any] = []
    for cfg in configs:
        kind = classify_steam_linked(cfg, steam_titles, id_map)
        if kind == 'steam' and not steam_library_cross_ref_enabled:
            continue
        out.append(cfg)
    return out


def load_steam_titles_for_cross_ref(enabled: bool) -> set[str]:
    """Load steam titles for cross ref."""
    if not enabled:
        return set()
    try:
        return UbisoftIdMap.get_steam_library_titles()
    except Exception as e:
        logger.warning('[Ubisoft] steam title scrape failed: %s', e)
        return set()


def classify_steam_linked(
    cfg: Any, steam_titles: set[str], id_map: UbisoftIdMap,
) -> str | None:
    """Classify steam linked.

    Returns:
        'steam' if the config is a Steam-linked variant,
        'cross_ref' if Steam owns a same-named title (cross-ref),
        None otherwise.
    """
    third_party = getattr(cfg, 'third_party_platform', '') or ''
    if 'steam' in third_party.lower():
        return 'steam'
    yaml_raw = getattr(cfg, 'yaml_raw', '') or ''
    if yaml_has_steam_markers(yaml_raw):
        return 'steam'
    if steam_titles:
        name = getattr(cfg, 'name', '') or ''
        if name and id_map.normalize_for_matching(name) in {
            id_map.normalize_for_matching(t) for t in steam_titles
        }:
            return 'cross_ref'
    return None


def yaml_has_steam_markers(yaml_raw: str) -> bool:
    """YAML has steam markers."""
    if not yaml_raw:
        return False
    text = yaml_raw.lower()
    return any(marker.lower() in text for marker in _STEAM_YAML_MARKERS)
