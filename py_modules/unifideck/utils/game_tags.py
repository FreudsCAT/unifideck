"""
Persistent store-tag storage for Unifideck games.

Maps ``store:game_id`` keys to a list of string tags that carry store-specific
metadata which needs to survive across syncs and be readable at runtime by
``get_game_info``.

Current tags
------------
``"not_compatible"``
    The game cannot be installed or run on Linux/Steam Deck.
    Currently assigned to Microsoft Store games whose only distribution
    format is MSIX/UWP (no Win32 / FE3-downloadable package exists).

``"play_anywhere"``
    The game carries Xbox Play Anywhere entitlement
    (``Sku.Properties.IsXboxPlayAnywhere`` in the MS catalog API).

Public API
----------
save_game_tags(store, game_id, tags)
    Persist the tag list for one game.  An empty / None list removes the key.

get_game_tags(store, game_id) -> List[str]
    Return the persisted tags, or [] if none.
"""

import json
import logging
import os
import tempfile
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_TAGS_FILE = os.path.expanduser("~/.local/share/unifideck/game_tags.json")

# In-memory cache to avoid hammering disk on every get_game_info call.
_cache: Optional[Dict[str, List[str]]] = None


def _load() -> Dict[str, List[str]]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        if os.path.exists(_TAGS_FILE):
            with open(_TAGS_FILE, "r") as f:
                data = json.load(f)
            _cache = {k: v for k, v in data.items() if isinstance(v, list)}
        else:
            _cache = {}
    except Exception as e:
        logger.debug(f"[GameTags] Could not read game_tags.json: {e}")
        _cache = {}
    return _cache


def _flush(data: Dict[str, List[str]]) -> None:
    """Atomic write of the entire tags dict."""
    os.makedirs(os.path.dirname(_TAGS_FILE), exist_ok=True)
    dir_name = os.path.dirname(_TAGS_FILE)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dir_name, delete=False,
            prefix=".game_tags.", suffix=".tmp"
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.rename(tmp_path, _TAGS_FILE)
    except Exception as e:
        logger.error(f"[GameTags] Atomic write failed: {e}")
        # Fallback
        try:
            with open(_TAGS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e2:
            logger.error(f"[GameTags] Fallback write also failed: {e2}")


def save_game_tags(store: str, game_id: str, tags: Optional[List[str]]) -> None:
    """
    Persist *tags* for a game.  Calling with an empty / None list removes the
    key so the file stays lean.

    Args:
        store:   Store identifier (e.g. ``'microsoft'``).
        game_id: Store-native game ID.
        tags:    List of tag strings, or None / [] to remove.
    """
    global _cache
    data = _load()
    key = f"{store}:{game_id}"

    if tags:
        data[key] = list(tags)
        logger.debug(f"[GameTags] Saved tags for {key}: {tags}")
    else:
        if key in data:
            del data[key]
            logger.debug(f"[GameTags] Removed tags for {key}")
        else:
            return  # Nothing to do

    _cache = data
    _flush(data)


def get_game_tags(store: str, game_id: str) -> List[str]:
    """
    Return the persisted tags for a game.

    Returns:
        List of tag strings; empty list if no tags are stored.
    """
    key = f"{store}:{game_id}"
    return _load().get(key, [])


def invalidate_cache() -> None:
    """Force a re-read from disk on the next call."""
    global _cache
    _cache = None
