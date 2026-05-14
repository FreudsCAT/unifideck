r"""services/shortcut/games_map.py — games.map data model + serialization.

Pure module: NamedTuple for a row + the 3 symbols that produce
or consume the ``games.map`` manifest. No I/O, no class state —
``ShortcutService`` composes via function calls.

Serialisation:
- v1: ``store:game_id=/path/to/exe``
- v2: ``store:game_id=/path/to/exe\t/path/to/workdir``

v2 adds explicit ``work_dir`` so the dispatcher doesn't have to
derive it from ``dirname(exe)`` + xCloud special casing. v1
entries still parse (lazy migration on next write).
"""
from __future__ import annotations

import binascii
import os
from typing import NamedTuple


class GameMapEntry(NamedTuple):
    r"""One entry in games.map (v2 format).

    Rules:
    - Tab separator because ``=`` can appear in exe paths;
      tabs are never legal in Linux/Windows paths.
    - v1 entries (no tab) are still valid input — the parser
      falls back to ``dirname(exe)`` as ``work_dir``.
    - xCloud sentinel: ``exe="xcloud"`` + URL in ``work_dir``
      signals the streaming trigger to the dispatcher.
    """
    exe: str
    work_dir: str


def generate_app_id(exe: str, title: str) -> int:
    """Compute deterministic 32-bit shortcut ID from exe + title.

    Matches Steam's internal algorithm: CRC32 of ``exe+title``
    with the top bit set (marks as non-Steam shortcut). Result
    returned as signed 32-bit to match how Steam stores it.
    Argument order matters — ``(exe, title)`` reversed produces
    a different hash and breaks Steam's matching.
    """
    # Create the concatenated string Steam uses
    key = exe + title

    # Calculate CRC32 and apply the Steam shortcut bitmask (0x80000000)
    crc = binascii.crc32(key.encode("utf-8")) | 0x80000000

    # Convert to signed 32-bit integer
    if crc > 0x7FFFFFFF:
        crc -= 0x100000000

    return crc


def parse_games_map(content: str) -> dict[str, GameMapEntry]:
    r"""Parse games.map content into ``{key: GameMapEntry}``.

    Accepts both v1 (``key=exe``) and v2
    (``key=exe\twork_dir``) — v1 entries get ``work_dir``
    derived from ``dirname(exe)`` as best-effort fallback.
    Malformed lines (no ``=``, empty values) and comments
    (``#`` prefix) / blank lines are silently skipped.
    """
    result: dict[str, GameMapEntry] = {}

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split("=", 1)
        if len(parts) != 2:
            continue

        key, value = parts
        key = key.strip()

        # Handle v2 format with explicit work_dir
        if "\t" in value:
            exe, work_dir = value.split("\t", 1)
            result[key] = GameMapEntry(exe=exe.strip(), work_dir=work_dir.strip())
        else:
            # Handle v1 format: derive work_dir from exe
            exe = value.strip()
            # Special case for xcloud in v1 format (rare but handled)
            if exe == "xcloud":
                work_dir = ""
            else:
                work_dir = os.path.dirname(exe)
            result[key] = GameMapEntry(exe=exe, work_dir=work_dir)

    return result


def format_games_map(mapping: dict[str, GameMapEntry]) -> str:
    r"""Serialize ``{key: GameMapEntry}`` to games.map v2 text.

    Always writes v2 format (``exe\twork_dir``), even when
    ``work_dir == dirname(exe)`` — makes the file
    self-describing so readers can tell v1 fallbacks from
    explicit work_dir by checking for the tab character.
    Sorted by key for reproducible output.
    """
    lines = [
        "# Unifideck non-Steam shortcut manifest (games.map)",
        "# Format: store:game_id=exe_path\\twork_dir",
        "# DO NOT EDIT manually. Managed by unifideck-decky.",
    ]

    for key in sorted(mapping.keys()):
        entry = mapping[key]
        lines.append(f"{key}={entry.exe}\t{entry.work_dir}")

    # Add trailing newline
    return "\n".join(lines) + "\n"
