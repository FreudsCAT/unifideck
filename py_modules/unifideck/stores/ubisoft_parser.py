"""
Ubisoft Binary File Parser

Parses UPC's binary configuration and ownership cache files to extract
game metadata (install_id, launch_id, space_id, executable paths, etc.).

Algorithm adapted from Lutris's proven parser (lutris/util/ubisoft/parser.py)
which uses sequential record reading with a sync correction fallback.

File locations within a Wine prefix:
  - configurations: {prefix}/drive_c/users/steamuser/AppData/Local/
                     Ubisoft Game Launcher/cache/configuration/configurations
  - ownership:      {prefix}/drive_c/users/steamuser/AppData/Local/
                     Ubisoft Game Launcher/cache/ownership/{userId}
"""
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Names that are placeholders in the YAML, not real game names
BLACKLISTED_NAMES = ["gamename", "l1", "l2", "thumbimage", "", "ubisoft game", "name"]


# ============================================================================
# Konrad's variable-length integer decoder
# ============================================================================

def _convert_data(data: int) -> int:
    """Decode a variable-length integer using Konrad's formula.

    UPC encodes multi-byte integers with continuation bits in the high
    position of each byte. This reverses that encoding.
    """
    if data > 256 * 256:
        data -= 128 * 256 * math.ceil(data / (256 * 256))
        data -= 128 * math.ceil(data / 256)
    elif data > 256:
        data -= 128 * math.ceil(data / 256)
    return data


# ============================================================================
# Configuration header parser
# ============================================================================

def _parse_config_header(header: bytes, second_eight: bool = False) -> tuple:
    """Parse a configuration record header, extracting size and IDs.

    Args:
        header: Raw bytes starting at a 0x0A record marker.
        second_eight: If True, use alternate parsing for sync correction
                      (looks for second 0x08 delimiter instead of first).

    Returns:
        (record_size, install_id, launch_id, header_size)
    """
    try:
        offset = 1
        multiplier = 1
        record_size = 0
        tmp_size = 0

        # Read record size (bytes until 0x08 delimiter)
        if second_eight:
            while (header[offset] != 0x08
                   or (header[offset] == 0x08 and header[offset + 1] == 0x08)):
                record_size += header[offset] * multiplier
                multiplier *= 256
                offset += 1
                tmp_size += 1
        else:
            while header[offset] != 0x08 or record_size == 0:
                record_size += header[offset] * multiplier
                multiplier *= 256
                offset += 1
                tmp_size += 1

        record_size = _convert_data(record_size)
        offset += 1  # skip 0x08

        # Read install_id (bytes until 0x10 delimiter)
        multiplier = 1
        install_id = 0
        while header[offset] != 0x10 or header[offset + 1] == 0x10:
            install_id += header[offset] * multiplier
            multiplier *= 256
            offset += 1
        install_id = _convert_data(install_id)
        offset += 1  # skip 0x10

        # Read launch_id (bytes until 0x1A delimiter)
        multiplier = 1
        launch_id = 0
        while (header[offset] != 0x1A
               or (header[offset] == 0x1A and header[offset + 1] == 0x1A)):
            launch_id += header[offset] * multiplier
            multiplier *= 256
            offset += 1
        launch_id = _convert_data(launch_id)

        # Size correction for records near a 128-byte boundary
        if record_size - offset < 128 <= record_size:
            tmp_size -= 1
            record_size += 1

        return record_size - offset, install_id, launch_id, offset + tmp_size + 1
    except Exception:
        return 0, 0, 0, 10


# ============================================================================
# YAML field extraction
# ============================================================================

def _get_yaml_field(game_yaml: dict, field: str = "name") -> str:
    """Extract a field from parsed game YAML with fallback chain.

    Fallback order for 'name' field:
      1. root.<field>
      2. root.installer.game_identifier  (if name is blacklisted)
      3. localizations.default[<field>]   (if still blacklisted)
    """
    value = ""
    root = game_yaml.get("root", {})
    if not isinstance(root, dict):
        return ""

    if field in root:
        value = str(root[field])

    # Fallback 1: installer.game_identifier
    if field == "name" and value.lower() in BLACKLISTED_NAMES:
        installer = root.get("installer", {})
        if isinstance(installer, dict) and "game_identifier" in installer:
            value = str(installer["game_identifier"])

    # Fallback 2: localizations.default
    if value.lower() in BLACKLISTED_NAMES:
        locs = game_yaml.get("localizations", {})
        if isinstance(locs, dict):
            default_loc = locs.get("default", {})
            if isinstance(default_loc, dict) and value in default_loc:
                value = str(default_loc[value])

    return value


# ============================================================================
# Configurations parser
# ============================================================================

class GameConfig:
    """Parsed game entry from the configurations binary."""

    def __init__(self):
        self.install_id: int = 0
        self.launch_id: int = 0
        self.space_id: str = ""
        self.name: str = ""
        self.executable: str = ""      # Relative path from game dir
        self.thumb_image: str = ""
        self.game_identifier: str = ""  # installer.game_identifier
        self.yaml_raw: str = ""         # Raw YAML text (for debugging)

    def __repr__(self) -> str:
        return (
            f"GameConfig(name={self.name!r}, space_id={self.space_id!r}, "
            f"install_id={self.install_id}, launch_id={self.launch_id})"
        )


def parse_configurations(filepath: str) -> List[GameConfig]:
    """Parse the UPC configurations binary file.

    Uses sequential record reading with a sync correction fallback
    (second_eight=True) when the next byte isn't 0x0A.

    Args:
        filepath: Absolute path to the configurations binary.

    Returns:
        List of GameConfig entries (may be empty on error).
    """
    if not os.path.isfile(filepath):
        logger.warning(f"[UbiParser] Configurations file not found: {filepath}")
        return []

    try:
        with open(filepath, "rb") as f:
            data = f.read()
    except Exception as e:
        logger.error(f"[UbiParser] Failed to read configurations: {e}")
        return []

    results: List[GameConfig] = []
    global_offset = 0

    while global_offset < len(data):
        chunk = data[global_offset:]
        obj_size, install_id, launch_id, header_size = _parse_config_header(chunk)
        launch_id = (install_id
                     if launch_id == 0 or launch_id == install_id
                     else launch_id)

        if obj_size > 500:
            yaml_start = global_offset + header_size
            yaml_end = yaml_start + obj_size
            if yaml_end <= len(data):
                stream = data[yaml_start:yaml_end].decode("utf8", errors="ignore")
                if stream and "start_game" in stream:
                    try:
                        parsed = yaml.load(
                            stream.replace("\t", " "),
                            Loader=yaml.FullLoader,
                        )
                        if parsed:
                            config = _build_game_config(
                                parsed, stream, install_id, launch_id
                            )
                            if config and config.name:
                                results.append(config)
                    except Exception as e:
                        logger.debug(
                            f"[UbiParser] YAML parse error at offset "
                            f"{global_offset}: {e}"
                        )

        global_offset_tmp = global_offset
        global_offset += obj_size + header_size

        # Sync correction: if next byte isn't 0x0A, re-parse with second_eight
        if (global_offset < len(data) and data[global_offset] != 0x0A):
            obj_size, _, _, header_size = _parse_config_header(chunk, True)
            global_offset = global_offset_tmp + obj_size + header_size

    logger.info(f"[UbiParser] Parsed {len(results)} game configs from {filepath}")
    return results


def _build_game_config(
    parsed: dict, yaml_text: str, install_id: int, launch_id: int
) -> Optional[GameConfig]:
    """Build a GameConfig from parsed YAML and header IDs."""
    config = GameConfig()
    config.install_id = install_id
    config.launch_id = launch_id
    config.yaml_raw = yaml_text

    config.name = _get_yaml_field(parsed, "name")
    config.thumb_image = _get_yaml_field(parsed, "thumb_image")

    root = parsed.get("root", {})
    if isinstance(root, dict):
        config.space_id = str(root.get("space_id", ""))
        installer = root.get("installer", {})
        if isinstance(installer, dict):
            config.game_identifier = str(installer.get("game_identifier", ""))

    # Extract executable path from start_game.online.executables[].path.relative
    exe_match = re.search(
        r"relative:\s*(.+?\.exe)",
        yaml_text,
        re.IGNORECASE,
    )
    if exe_match:
        config.executable = exe_match.group(1).strip().strip("'\"")

    return config


# ============================================================================
# Ownership parser
# ============================================================================

def parse_ownership(filepath: str) -> List[int]:
    """Parse the UPC ownership binary file.

    Returns a list of owned install_ids (may contain duplicates).

    Args:
        filepath: Absolute path to the ownership binary
                  (e.g., ``{prefix}/.../cache/ownership/{userId}``).

    Returns:
        List of integer install_ids.
    """
    if not os.path.isfile(filepath):
        logger.warning(f"[UbiParser] Ownership file not found: {filepath}")
        return []

    try:
        with open(filepath, "rb") as f:
            data = f.read()
    except Exception as e:
        logger.error(f"[UbiParser] Failed to read ownership: {e}")
        return []

    owned: List[int] = []
    offset = 0x108  # Ownership records start after a 264-byte header

    while offset < len(data):
        chunk = data[offset:]
        if chunk[0] != 0x0A:
            break

        try:
            pos = 1
            multiplier = 1
            rec_size = 0
            tmp_size = 0

            # Read record size (bytes until 0x08)
            while chunk[pos] != 0x08 or rec_size == 0:
                rec_size += chunk[pos] * multiplier
                multiplier *= 256
                pos += 1
                tmp_size += 1
            rec_size = _convert_data(rec_size)
            pos += 1  # skip 0x08

            # Read first ID (bytes until 0x10)
            multiplier = 1
            lid1 = 0
            while chunk[pos] != 0x10 or chunk[pos + 1] == 0x10:
                lid1 += chunk[pos] * multiplier
                multiplier *= 256
                pos += 1
            lid1 = _convert_data(lid1)
            pos += 1  # skip 0x10

            # Read second ID (bytes until 0x22)
            multiplier = 1
            lid2 = 0
            while chunk[pos] != 0x22:
                lid2 += chunk[pos] * multiplier
                multiplier *= 256
                pos += 1
            lid2 = _convert_data(lid2)

            owned.append(lid1)
            if lid2 != lid1:
                owned.append(lid2)

            offset += rec_size + tmp_size + 1
        except Exception:
            break

    logger.info(f"[UbiParser] Found {len(owned)} owned IDs in {filepath}")
    return owned


# ============================================================================
# Install state checker
# ============================================================================

def check_install_state(state_file: str) -> bool:
    """Check if a game's ``uplay_install.state`` indicates completion.

    A first byte of ``0x0A`` means the game is fully installed.
    """
    if not os.path.isfile(state_file):
        return False

    try:
        with open(state_file, "rb") as f:
            first_byte = f.read(1)
        return first_byte == b"\x0a"
    except Exception:
        return False


# ============================================================================
# High-level helpers
# ============================================================================

def build_id_map_from_configurations(filepath: str) -> Dict[str, Dict[str, Any]]:
    """Parse configurations and build a space_id -> {install_id, launch_id, name, exe} map.

    Used to populate ``ubisoft_id_map.json`` with resolved IDs
    so the launcher script can look them up without re-parsing the binary.
    """
    configs = parse_configurations(filepath)
    id_map: Dict[str, Dict[str, Any]] = {}

    for cfg in configs:
        if not cfg.space_id:
            continue

        id_map[cfg.space_id] = {
            "install_id": str(cfg.install_id),
            "launch_id": str(cfg.launch_id),
            "name": cfg.name,
            "executable": cfg.executable,
            "game_identifier": cfg.game_identifier,
        }

    logger.info(f"[UbiParser] Built ID map with {len(id_map)} entries")
    return id_map
