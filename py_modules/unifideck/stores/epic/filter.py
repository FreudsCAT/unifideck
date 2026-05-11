"""filter.py — Drop UE assets, plugins, mods, and mobile-only entries.

# OP-48f | py_modules/unifideck/stores/epic/filter.py | Depends: OP-48c

Mirrors Heroic Games Launcher's library filter. Without it the user
sees thousands of free UE Marketplace assets they don't own as games.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)
ASSET_CATEGORIES: set[str] = {'assets', 'asset-format', 'plugins', 'projects'}
MOBILE_PLATFORMS: set[str] = {'Android', 'iOS'}


def has_ue_namespace(metadata: dict[str, Any]) -> bool:
    """Has UE namespace."""
    return metadata.get('namespace') == 'ue'


def has_asset_category(metadata: dict[str, Any]) -> bool:
    """Has asset category."""
    categories = metadata.get('categories') or []
    for cat in categories:
        if isinstance(cat, dict) and cat.get('path') in ASSET_CATEGORIES:
            return True
    return False


def has_mod_category(metadata: dict[str, Any]) -> bool:
    """Has mod category."""
    categories = metadata.get('categories') or []
    for cat in categories:
        if isinstance(cat, dict) and cat.get('path') == 'mods':
            return True
    return False


def is_mobile_only(metadata: dict[str, Any]) -> bool:
    """Is mobile only."""
    release_info = metadata.get('releaseInfo') or []
    if not release_info:
        return False
    for info in release_info:
        platforms = info.get('platform') if isinstance(info, dict) else None
        if not platforms:
            return False
        if not all(p in MOBILE_PLATFORMS for p in platforms):
            return False
    return True


def should_filter_epic_item(game_data: dict[str, Any]) -> bool:
    """Should filter epic item."""
    metadata = game_data.get('metadata') or {}
    if not isinstance(metadata, dict):
        return False
    if has_ue_namespace(metadata):
        return True
    if has_asset_category(metadata):
        return True
    if has_mod_category(metadata):
        return True
    if is_mobile_only(metadata):
        return True
    return False
