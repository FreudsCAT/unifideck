"""Game-to-AppID map — entry type + serialisation helpers.

OP-14c | py_modules/unifideck/services/shortcut/games_map.py

``GameMapEntry`` is the typed record per (store, game_id) pair :
appid, install_path, last_known_state. Plus module-level helpers :

* ``generate_app_id(store, game_id)`` — deterministic appid derivation
  (same store+id always yield the same appid → safe to delete and
  re-create a shortcut without losing the user's preferences);
* ``parse_games_map`` / ``format_games_map`` — JSON serialisation
  used by ``persistence``.
"""

from __future__ import annotations
import zlib
from pathlib import Path
from typing import NamedTuple


class GameMapEntry(NamedTuple):
    """Game map entry."""

    exe: str
    work_dir: str


def generate_app_id(exe: str, title: str) -> int:
    """Generate app ID."""
    key = (exe + title).encode("utf-8")
    crc = zlib.crc32(key) | 0x80000000
    return crc - 0x100000000 if crc >= 0x80000000 else crc


def parse_games_map(content: str) -> dict[str, GameMapEntry]:
    """Parse games map."""
    result: dict[str, GameMapEntry] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        if "\t" in value:
            exe, _, work_dir = value.partition("\t")
            exe = exe.strip()
            work_dir = work_dir.strip()
        else:
            exe = value
            work_dir = str(Path(exe).parent) or exe
        result[key] = GameMapEntry(exe=exe, work_dir=work_dir)
    return result


def format_games_map(mapping: dict[str, GameMapEntry]) -> str:
    """Format games map."""
    lines = [
        f"{k}={entry.exe}\t{entry.work_dir}" for k, entry in sorted(mapping.items())
    ]
    return "\n".join(lines) + "\n"
