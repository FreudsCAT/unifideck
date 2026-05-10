"""parser.py — High-level UPC binary cache parsing.

# OP-55e | py_modules/unifideck/stores/ubisoft/parser.py | Depends: (none)

Walks the ``configurations`` and ``ownership`` files UPC writes inside
its game-launcher cache and turns them into structured records (see
:class:`GameConfig`). YAML payloads embedded in each record are parsed
with PyYAML where present and fall back to permissive regex extraction
for truncated chunks.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, cast

try:
    import yaml as _yaml  # noqa: F401  PyYAML is bundled by Decky.
    _HAS_YAML = True
except Exception:  # pragma: no cover - degraded mode
    _HAS_YAML = False

from .parser_binary import (
    parse_install_id,
    parse_launch_id,
    parse_ownership_record,
    parse_record_size,
)

logger = logging.getLogger(__name__)
BLACKLISTED_NAMES = [
    'gamename', 'l1', 'l2', 'thumbimage', '', 'ubisoft game', 'name',
]


def _parse_config_header(
    header: bytes, second_eight: bool = False,
) -> tuple:
    """Parse the install_id / launch_id / object-size triple at the
    start of a configuration record. Returns (install_id, launch_id,
    obj_size, header_bytes_consumed).
    """
    offset = 0
    install_id, c1 = parse_install_id(header, offset)
    offset += c1
    launch_id, c2 = parse_launch_id(header, offset)
    offset += c2
    obj_size, c3, _raw = parse_record_size(header, offset, second_eight)
    offset += c3
    return install_id, launch_id, obj_size, offset


def _get_yaml_field(game_yaml: dict, field: str = 'name') -> str:
    """Extract a top-level field from a parsed game YAML, with the
    UPC-specific fallbacks the launcher applies at runtime.
    """
    if not isinstance(game_yaml, dict):
        return ""
    current = game_yaml.get(field, "") or ""
    if isinstance(current, str):
        current = current.strip()
    else:
        current = str(current)
    if field == 'name':
        current = _yaml_field_localization_fallback(game_yaml, current)
        current = _yaml_field_installer_fallback(game_yaml, current)
    return current


def _yaml_field_installer_fallback(root: dict, current: str) -> str:
    """Pull a name out of installer.game_identifier when the top-level
    name is empty or a placeholder.
    """
    if current and current.lower() not in BLACKLISTED_NAMES:
        return current
    installer = root.get('installer') if isinstance(root, dict) else None
    if isinstance(installer, dict):
        candidate = installer.get('game_identifier') or installer.get('name')
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return current


def _yaml_field_localization_fallback(game_yaml: dict, current: str) -> str:
    """Some titles only set the human name in localizations.default.GAMENAME."""
    if current and current.lower() not in BLACKLISTED_NAMES:
        return current
    loc = game_yaml.get('localizations') if isinstance(game_yaml, dict) else None
    if isinstance(loc, dict):
        default = loc.get('default')
        if isinstance(default, dict):
            for key in ('GAMENAME', 'gamename', 'NAME', 'name'):
                value = default.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return current


class GameConfig:
    """Parsed game entry from the configurations binary."""

    def __init__(self):
        """Initialize the instance."""
        self.install_id: int = 0
        self.launch_id: int = 0
        self.space_id: str = ""
        self.name: str = ""
        self.executable: str = ""
        self.thumb_image: str = ""
        self.game_identifier: str = ""
        self.yaml_raw: str = ""
        self.third_party_platform: str = ""

    def __repr__(self) -> str:
        """Repr."""
        return (
            f"GameConfig(name={self.name!r}, space_id={self.space_id!r}, "
            f"install_id={self.install_id}, launch_id={self.launch_id})"
        )


def _read_binary_file(filepath: str) -> bytes | None:
    """Read a binary file, returning ``None`` on any I/O error."""
    if not os.path.isfile(filepath):
        logger.warning("[UbiParser] file not found: %s", filepath)
        return None
    try:
        with open(filepath, "rb") as f:
            return f.read()
    except OSError as e:
        logger.error("[UbiParser] failed to read %s: %s", filepath, e)
        return None


def _extract_config_chunk(
    data: bytes,
    global_offset: int,
    header_size: int,
    obj_size: int,
    install_id: int,
    launch_id: int,
) -> GameConfig | None:
    """Extract one chunk's worth of YAML and convert it to a GameConfig."""
    chunk_start = global_offset + header_size
    chunk_end = chunk_start + obj_size
    if chunk_end > len(data) or obj_size < 50:
        return None
    chunk = data[chunk_start:chunk_end]
    yaml_start = chunk.find(b"\x1A")
    if yaml_start < 0:
        return None
    yaml_bytes = chunk[yaml_start + 1:]
    text = yaml_bytes.decode("utf-8", errors="replace")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    if "start_game" not in text:
        return None
    parsed: dict[str, Any] = {}
    if _HAS_YAML:
        try:
            loaded = _yaml.safe_load(text)
            if isinstance(loaded, dict):
                # The YAML is shaped {<install_id>: {root: {…}}}.
                for value in loaded.values():
                    if isinstance(value, dict) and 'root' in value:
                        parsed = cast(dict, value['root'])
                        break
                if not parsed:
                    parsed = loaded
        except Exception as e:
            logger.debug("[UbiParser] yaml parse failed: %s", e)
    return _build_game_config(parsed, text, install_id, launch_id)


def parse_configurations(filepath: str) -> list[GameConfig]:
    """Parse the UPC configurations binary into a list of GameConfig.

    Walks each 0x0A-rooted record, decodes the install_id / launch_id /
    object-size header, and extracts the embedded YAML chunk.
    """
    data = _read_binary_file(filepath)
    if data is None:
        return []
    results: list[GameConfig] = []
    offset = 0
    while offset < len(data):
        if data[offset] != 0x0A:
            offset += 1
            continue
        record_start = offset
        offset += 1
        try:
            install_id, launch_id, obj_size, header_consumed = (
                _parse_config_header(data[offset:offset + 32])
            )
        except Exception as e:
            logger.debug("[UbiParser] header parse @ %d: %s", record_start, e)
            offset = record_start + 1
            continue
        if obj_size < 50:
            offset = record_start + 1
            continue
        cfg = _extract_config_chunk(
            data, record_start, 1 + header_consumed,
            obj_size, install_id, launch_id,
        )
        if cfg is not None and cfg.name:
            results.append(cfg)
        offset = record_start + 1 + header_consumed + obj_size
    logger.info("[UbiParser] parsed %d configs from %s", len(results), filepath)
    return results


def _build_game_config(
    parsed: dict, yaml_text: str, install_id: int, launch_id: int,
) -> GameConfig | None:
    """Lift the chosen scalar fields off the parsed YAML."""
    cfg = GameConfig()
    cfg.install_id = install_id
    cfg.launch_id = launch_id
    cfg.yaml_raw = yaml_text
    cfg.name = _get_yaml_field(parsed, 'name')
    cfg.space_id = _get_yaml_field(parsed, 'space_id')
    cfg.thumb_image = _get_yaml_field(parsed, 'thumb_image')
    installer = parsed.get('installer') if isinstance(parsed, dict) else None
    if isinstance(installer, dict):
        identifier = installer.get('game_identifier')
        if isinstance(identifier, str):
            cfg.game_identifier = identifier.strip()
    start_game = parsed.get('start_game') if isinstance(parsed, dict) else None
    if isinstance(start_game, dict):
        cfg.executable = _resolve_executable(start_game)
    if not cfg.executable:
        match = re.search(r"relative:\s*(.+?\.exe)", yaml_text, re.IGNORECASE)
        if match:
            cfg.executable = match.group(1).strip().strip("'\"")
    cfg.third_party_platform = _extract_third_party_platform(parsed, installer)
    if not cfg.space_id:
        match = re.search(r"space_id:\s*([a-f0-9\-]+)", yaml_text)
        if match:
            cfg.space_id = match.group(1)
    return cfg if cfg.name else None


def _resolve_executable(start_game: dict) -> str:
    """Walk start_game.online.executables[].path.relative for an .exe."""
    for branch_key in ('online', 'offline', 'steam'):
        branch = start_game.get(branch_key)
        if not isinstance(branch, dict):
            continue
        executables = branch.get('executables')
        if not isinstance(executables, list):
            continue
        for entry in executables:
            if not isinstance(entry, dict):
                continue
            path = entry.get('path')
            if isinstance(path, dict):
                rel = path.get('relative')
                if isinstance(rel, str) and rel.lower().endswith('.exe'):
                    return rel.strip().strip("'\"")
    return ""


def _extract_third_party_platform(root: dict, installer: Any) -> str:
    """Tag known third-party platform configurations (Steam, EGS, etc.)."""
    if isinstance(installer, dict):
        platform = installer.get('third_party_platform')
        if isinstance(platform, str):
            return platform.strip()
    if isinstance(root, dict):
        third = root.get('third_party_platform')
        if isinstance(third, str):
            return third.strip()
    return ""


def parse_ownership(filepath: str) -> list[int]:
    """Parse the UPC ownership binary into a list of owned launch_ids."""
    data = _read_ownership_file(filepath)
    if data is None:
        return []
    owned: list[int] = []
    offset = 0x108
    while offset < len(data):
        if data[offset] != 0x0A:
            offset += 1
            continue
        offset += 1
        rec_size, consumed, _raw = parse_record_size(data, offset, False)
        offset += consumed
        chunk_end = min(offset + rec_size, len(data))
        chunk = data[offset:chunk_end]
        record = parse_ownership_record(chunk)
        if record is not None:
            owned.append(record[0])
        offset = chunk_end
    logger.info("[UbiParser] %d owned IDs in %s", len(owned), filepath)
    return owned


def _read_ownership_file(filepath: str) -> bytes | None:
    """Read the ownership cache, tolerating absence."""
    return _read_binary_file(filepath)


def check_install_state(state_file: str) -> bool:
    """Return True when ``uplay_install.state`` indicates a complete install."""
    if not os.path.isfile(state_file):
        return False
    try:
        with open(state_file, "rb") as f:
            return f.read(1) == b"\x0A"
    except OSError:
        return False


def build_id_map_from_configurations(filepath: str) -> dict[str, dict[str, Any]]:
    """Convenience: ``parse_configurations`` flattened to a space_id keyed
    map for direct merge into ``ubisoft_id_map.json``.
    """
    id_map: dict[str, dict[str, Any]] = {}
    for cfg in parse_configurations(filepath):
        if not cfg.space_id:
            continue
        id_map[cfg.space_id] = {
            "install_id": str(cfg.install_id),
            "launch_id": str(cfg.launch_id),
            "name": cfg.name,
            "executable": cfg.executable,
            "game_identifier": cfg.game_identifier,
        }
    logger.info("[UbiParser] built id_map of %d entries", len(id_map))
    return id_map
