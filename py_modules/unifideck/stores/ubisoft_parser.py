"""
Ubisoft Binary File Parser

Parses UPC's binary configuration and ownership cache files to extract
game metadata (install_id, launch_id, executable paths, etc.).

File locations within a Wine prefix:
  - configurations: {prefix}/drive_c/Program Files (x86)/Ubisoft/
                     Ubisoft Game Launcher/cache/configuration/configurations
  - ownership:      {prefix}/drive_c/Program Files (x86)/Ubisoft/
                     Ubisoft Game Launcher/cache/ownership/{userId}

Reference: docs/ubisoft-store-spec.md Appendix B
"""
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Konrad's variable-length integer decoder
# ============================================================================

def _decode_varint(data: int) -> int:
    """
    Decode a variable-length integer from UPC's binary format.

    UPC uses a compact varint encoding where high bits in each byte
    indicate continuation. This formula reverses that encoding.

    Reference: docs/ubisoft-store-spec.md Appendix B.1, "Konrad's formula"
    """
    if data > 256 * 256:
        data -= 128 * 256 * math.ceil(data / (256 * 256))
        data -= 128 * math.ceil(data / 256)
    elif data > 256:
        data -= 128 * math.ceil(data / 256)
    return data


def _read_varint_at(buf: bytes, offset: int) -> tuple:
    """
    Read a variable-length integer starting at *offset* in *buf*.

    Returns (decoded_value, bytes_consumed).
    """
    raw = 0
    consumed = 0
    shift = 0
    while offset + consumed < len(buf):
        byte = buf[offset + consumed]
        raw |= (byte & 0x7F) << shift
        consumed += 1
        shift += 7
        if not (byte & 0x80):
            break
    return (raw, consumed)


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
    """
    Parse the UPC ``configurations`` binary file.

    Returns a list of :class:`GameConfig` objects — one per playable game
    entry (records that contain ``start_game`` in the YAML portion).

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
    offset = 0

    while offset < len(data):
        # Look for 0x0A record header
        if data[offset] != 0x0A:
            offset += 1
            continue

        try:
            record_start = offset
            offset += 1  # skip 0x0A

            # Read object size varint
            obj_size, consumed = _read_varint_at(data, offset)
            obj_size = _decode_varint(obj_size)
            offset += consumed

            # Only process records with meaningful size (games have > 500 bytes)
            if obj_size < 500:
                continue

            record_end = record_start + obj_size + 1 + consumed
            if record_end > len(data):
                break

            record_data = data[record_start + 1 + consumed: record_end]

            config = _parse_single_record(record_data)
            if config and config.name:
                results.append(config)

        except Exception as e:
            logger.debug(f"[UbiParser] Skipping malformed record at offset {record_start}: {e}")
            offset += 1
            continue

    logger.info(f"[UbiParser] Parsed {len(results)} game configs from {filepath}")
    return results


def _parse_single_record(record_data: bytes) -> Optional[GameConfig]:
    """
    Parse a single record from the configurations binary.

    Extracts install_id, launch_id from the binary header, and then
    looks for embedded YAML containing game metadata.
    """
    config = GameConfig()
    pos = 0

    try:
        # Read install_id (after 0x08 marker)
        if pos < len(record_data) and record_data[pos] == 0x08:
            pos += 1
            install_id, consumed = _read_varint_at(record_data, pos)
            config.install_id = _decode_varint(install_id)
            pos += consumed

        # Read launch_id (after 0x10 marker)
        if pos < len(record_data) and record_data[pos] == 0x10:
            pos += 1
            launch_id, consumed = _read_varint_at(record_data, pos)
            config.launch_id = _decode_varint(launch_id)
            pos += consumed

        # The rest contains YAML data (after 0x1A marker + size)
        yaml_text = _extract_yaml_from_record(record_data, pos)
        if not yaml_text:
            return None

        config.yaml_raw = yaml_text

        # Only include records with start_game (actual playable games)
        if "start_game" not in yaml_text:
            return None

        # Parse YAML fields using regex (avoid PyYAML dependency)
        config.name = _yaml_extract(yaml_text, r"(?:^|\n)\s*name:\s*(.+?)(?:\n|$)")
        config.space_id = _yaml_extract(yaml_text, r"space_id:\s*([a-f0-9\-]+)")
        config.thumb_image = _yaml_extract(yaml_text, r"thumb_image:\s*(.+?)(?:\n|$)")
        config.game_identifier = _yaml_extract(
            yaml_text, r"game_identifier:\s*(.+?)(?:\n|$)"
        )

        # Extract executable path from start_game.online.executables[].path.relative
        exe_match = re.search(
            r"relative:\s*(.+?\.exe)",
            yaml_text,
            re.IGNORECASE,
        )
        if exe_match:
            config.executable = exe_match.group(1).strip().strip("'\"")

        # Try localized name
        localized_name = _yaml_extract(
            yaml_text, r"GAMENAME:\s*(.+?)(?:\n|$)"
        )
        if localized_name:
            config.name = localized_name

    except Exception as e:
        logger.debug(f"[UbiParser] Error parsing record: {e}")
        return None

    return config


def _extract_yaml_from_record(record_data: bytes, start_pos: int) -> Optional[str]:
    """
    Extract the YAML text portion from a binary record.

    Looks for 0x1A markers followed by length-prefixed string data
    and decodes any ASCII/UTF-8 content.
    """
    try:
        # Find the first 0x1A byte (marks start of string/YAML data)
        yaml_start = -1
        for i in range(start_pos, len(record_data)):
            if record_data[i] == 0x1A:
                yaml_start = i + 1
                break

        if yaml_start < 0:
            return None

        # Read the length varint
        length, consumed = _read_varint_at(record_data, yaml_start)
        length = _decode_varint(length)
        yaml_start += consumed

        if yaml_start + length > len(record_data):
            # Try to decode what we have
            raw = record_data[yaml_start:]
        else:
            raw = record_data[yaml_start: yaml_start + length]

        # Decode, filtering non-printable characters
        text = raw.decode("utf-8", errors="replace")

        # Remove null bytes and control characters (except newline/tab)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

        return text if text.strip() else None

    except Exception:
        return None


def _yaml_extract(text: str, pattern: str) -> str:
    """Extract a single value from YAML-like text using regex."""
    match = re.search(pattern, text)
    if match:
        return match.group(1).strip().strip("'\"")
    return ""


# ============================================================================
# Ownership parser
# ============================================================================

def parse_ownership(filepath: str) -> List[int]:
    """
    Parse the UPC ``ownership`` binary file.

    Returns a list of owned launch_ids.

    Args:
        filepath: Absolute path to the ownership binary
                  (e.g., ``{prefix}/.../cache/ownership/{userId}``).

    Returns:
        List of integer launch_ids.
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

    # Ownership records start at offset 0x108
    owned_ids: List[int] = []
    offset = 0x108

    while offset < len(data):
        if data[offset] != 0x0A:
            offset += 1
            continue

        try:
            offset += 1  # skip 0x0A

            # Record size
            rec_size, consumed = _read_varint_at(data, offset)
            rec_size = _decode_varint(rec_size)
            offset += consumed

            # Read launch_id (after 0x08 marker)
            if offset < len(data) and data[offset] == 0x08:
                offset += 1
                launch_id, consumed = _read_varint_at(data, offset)
                launch_id = _decode_varint(launch_id)
                offset += consumed
                owned_ids.append(launch_id)

            # Skip launch_id_2 (after 0x10 marker)
            if offset < len(data) and data[offset] == 0x10:
                offset += 1
                _, consumed = _read_varint_at(data, offset)
                offset += consumed

            # Skip to end marker 0x22
            while offset < len(data) and data[offset] != 0x0A:
                offset += 1

        except Exception as e:
            logger.debug(f"[UbiParser] Skipping ownership record: {e}")
            offset += 1

    logger.info(f"[UbiParser] Found {len(owned_ids)} owned IDs in {filepath}")
    return owned_ids


# ============================================================================
# Install state checker
# ============================================================================

def check_install_state(state_file: str) -> bool:
    """
    Check if a game's ``uplay_install.state`` indicates completion.

    A first byte of ``0x0A`` means the game is fully installed.

    Args:
        state_file: Path to the ``uplay_install.state`` file.

    Returns:
        True if installed, False otherwise.
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
    """
    Parse configurations and build a space_id → {install_id, launch_id, name, exe} map.

    This is used to populate ``ubisoft_id_map.json`` with resolved IDs
    so the launcher script can look them up without re-parsing the binary.

    Args:
        filepath: Path to the configurations binary.

    Returns:
        Dict mapping space_id to {install_id, launch_id, name, executable}.
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
