"""
Steam library enumeration — reads local VDF files to discover ALL owned games.

Uses two data sources:
1. librarycache/ directories → owned app IDs (most complete, includes uninstalled)
2. appinfo.vdf binary → app_id ↔ name mapping (Steam's metadata cache)
"""
import logging
import os
import struct
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

# Steam root candidates (Linux/Steam Deck)
_STEAM_ROOTS = [
    Path.home() / ".steam" / "steam",
    Path.home() / ".local" / "share" / "Steam",
]


def _find_steam_root() -> Optional[Path]:
    for root in _STEAM_ROOTS:
        if (root / "appcache" / "appinfo.vdf").is_file():
            return root
    return None


def get_owned_app_ids() -> Set[int]:
    """Return app IDs for ALL games the user owns/has in their Steam library.

    Reads directory names from Steam's librarycache/ — each directory
    corresponds to a game the user has access to (installed or not).
    """
    steam_root = _find_steam_root()
    if not steam_root:
        return set()

    cache_dir = steam_root / "appcache" / "librarycache"
    if not cache_dir.is_dir():
        return set()

    app_ids: Set[int] = set()
    try:
        for entry in os.listdir(cache_dir):
            if entry.isdigit():
                app_ids.add(int(entry))
    except OSError as e:
        logger.debug(f"[Steam] Failed to list librarycache: {e}")

    logger.debug(f"[Steam] librarycache: {len(app_ids)} owned app IDs")
    return app_ids


def get_app_names(app_ids: Optional[Set[int]] = None) -> Dict[int, str]:
    """Extract game names from Steam's appinfo.vdf for the given app IDs.

    If app_ids is None, returns names for ALL apps (slower).
    Returns {app_id: game_name}.
    """
    steam_root = _find_steam_root()
    if not steam_root:
        return {}

    appinfo_path = steam_root / "appcache" / "appinfo.vdf"
    if not appinfo_path.is_file():
        return {}

    try:
        with open(appinfo_path, "rb") as f:
            data = f.read()

        magic = struct.unpack_from("<I", data, 0)[0]
        if magic == 0x07564429:
            return _extract_names_v29(data, app_ids)
        elif magic in (0x07564427, 0x07564428):
            return _extract_names_v27(data, app_ids)
        else:
            logger.debug(f"[Steam] Unsupported appinfo.vdf version: 0x{magic:08x}")
            return {}
    except Exception as e:
        logger.debug(f"[Steam] Failed to parse appinfo.vdf: {e}")
        return {}


def get_steam_library_names() -> Set[str]:
    """Return normalized game names for ALL games in the user's Steam library.

    This is the main entry point — combines librarycache app IDs with
    appinfo.vdf name resolution.
    """
    owned_ids = get_owned_app_ids()
    if not owned_ids:
        return set()

    names = get_app_names(owned_ids)
    logger.debug(
        f"[Steam] Resolved {len(names)} names out of {len(owned_ids)} owned app IDs"
    )
    return set(names.values())


# ============================================================================
# appinfo.vdf binary parsers (minimal — name extraction only)
# ============================================================================

def _extract_names_v29(
    data: bytes, wanted: Optional[Set[int]]
) -> Dict[int, str]:
    """Parse v29 appinfo.vdf and extract app_id → name for wanted IDs."""
    string_table_offset = struct.unpack_from("<Q", data, 8)[0]

    # Parse string table
    st_off = string_table_offset
    string_count = struct.unpack_from("<I", data, st_off)[0]
    st_off += 4
    strings = []
    for _ in range(string_count):
        end = data.index(b"\x00", st_off)
        strings.append(data[st_off:end].decode("utf-8", errors="replace"))
        st_off = end + 1

    result: Dict[int, str] = {}
    offset = 16
    while offset < string_table_offset:
        app_id = struct.unpack_from("<I", data, offset)[0]
        if app_id == 0:
            break
        offset += 4 + 64  # header

        if wanted is not None and app_id not in wanted:
            # Skip this entry's sections quickly
            offset = _skip_vdf_indexed(data, offset)
        else:
            name = _find_name_indexed(data, offset, strings)
            offset = _skip_vdf_indexed(data, offset)
            if name:
                result[app_id] = name

    return result


def _extract_names_v27(
    data: bytes, wanted: Optional[Set[int]]
) -> Dict[int, str]:
    """Parse v27/v28 appinfo.vdf and extract app_id → name for wanted IDs."""
    result: Dict[int, str] = {}
    offset = 8
    while True:
        app_id = struct.unpack_from("<I", data, offset)[0]
        if app_id == 0:
            break
        offset += 4 + 44  # header

        if wanted is not None and app_id not in wanted:
            offset = _skip_vdf_inline(data, offset)
        else:
            name = _find_name_inline(data, offset)
            offset = _skip_vdf_inline(data, offset)
            if name:
                result[app_id] = name

    return result


def _find_name_indexed(data: bytes, offset: int, strings: list) -> Optional[str]:
    """Walk indexed VDF sections looking for appinfo.common.name."""
    # Structure: SECTION("appinfo") → SECTION("common") → STRING("name")
    depth = 0
    in_common = False
    while offset < len(data):
        tb = data[offset]
        offset += 1
        if tb == 0x08:  # SECTION_END
            if in_common and depth == 2:
                return None  # Left common without finding name
            depth -= 1
            if depth < 0:
                break
            continue

        key_idx = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        key = strings[key_idx] if key_idx < len(strings) else ""

        if tb == 0x00:  # SECTION
            depth += 1
            if depth == 2 and key == "common":
                in_common = True
            elif in_common and depth > 2:
                # Skip nested sections inside common
                offset = _skip_one_section_indexed(data, offset)
                depth -= 1
        elif tb == 0x01:  # STRING
            end = data.index(b"\x00", offset)
            if in_common and key == "name":
                return data[offset:end].decode("utf-8", errors="replace")
            offset = end + 1
        elif tb == 0x02:  # INT32
            offset += 4
        elif tb == 0x07:  # INT64
            offset += 8

    return None


def _find_name_inline(data: bytes, offset: int) -> Optional[str]:
    """Walk inline VDF sections looking for appinfo.common.name."""
    depth = 0
    in_common = False
    while offset < len(data):
        tb = data[offset]
        offset += 1
        if tb == 0x08:
            if in_common and depth == 2:
                return None
            depth -= 1
            if depth < 0:
                break
            continue

        end = data.index(b"\x00", offset)
        key = data[offset:end].decode("utf-8", errors="replace")
        offset = end + 1

        if tb == 0x00:
            depth += 1
            if depth == 2 and key == "common":
                in_common = True
            elif in_common and depth > 2:
                offset = _skip_one_section_inline(data, offset)
                depth -= 1
        elif tb == 0x01:
            end = data.index(b"\x00", offset)
            if in_common and key == "name":
                return data[offset:end].decode("utf-8", errors="replace")
            offset = end + 1
        elif tb == 0x02:
            offset += 4
        elif tb == 0x07:
            offset += 8

    return None


def _skip_vdf_indexed(data: bytes, offset: int) -> int:
    """Skip an entire VDF section tree (indexed keys)."""
    depth = 0
    while offset < len(data):
        tb = data[offset]
        offset += 1
        if tb == 0x08:
            if depth == 0:
                break
            depth -= 1
            continue
        offset += 4  # key index
        if tb == 0x00:
            depth += 1
        elif tb == 0x01:
            offset = data.index(b"\x00", offset) + 1
        elif tb == 0x02:
            offset += 4
        elif tb == 0x07:
            offset += 8
    return offset


_skip_one_section_indexed = _skip_vdf_indexed


def _skip_vdf_inline(data: bytes, offset: int) -> int:
    """Skip an entire VDF section tree (inline keys)."""
    depth = 0
    while offset < len(data):
        tb = data[offset]
        offset += 1
        if tb == 0x08:
            if depth == 0:
                break
            depth -= 1
            continue
        offset = data.index(b"\x00", offset) + 1  # key string
        if tb == 0x00:
            depth += 1
        elif tb == 0x01:
            offset = data.index(b"\x00", offset) + 1
        elif tb == 0x02:
            offset += 4
        elif tb == 0x07:
            offset += 8
    return offset


_skip_one_section_inline = _skip_vdf_inline
