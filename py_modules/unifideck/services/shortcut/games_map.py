r"""services/shortcut/games_map.py — games.map data model + serialization.

Pure module: NamedTuple for a row + the 3 symbols that produce
or consume the ``games.map`` manifest. No I/O, no class state —
``ShortcutService`` composes via function calls.

Serialisation:
- v1: ``store:game_id=/path/to/exe``
- v2: ``store:game_id=/path/to/exe\t/path/to/workdir``
- v3: ``store:game_id=/path/to/exe\t/path/to/workdir\t<signed_app_id>``

v2 adds explicit ``work_dir`` so the dispatcher doesn't have to
derive it from ``dirname(exe)`` + xCloud special casing. v3 adds
the canonical Steam app_id so cleanup can locate entries to drop
without recomputing the hash with the wrong title. Older entries
still parse — ``app_id`` defaults to ``0`` and is backfilled by
the shortcut service on next save.
"""
from __future__ import annotations

import binascii
from pathlib import Path
from typing import NamedTuple

# Sentinel tag written into Steam's shortcut ``tags`` dict to mark
# entries owned by Unifideck. Lives here (a leaf module) so both
# ``games_map_mixin`` and ``reconcile_phases`` can import it without
# closing the import cycle that previously existed between them.
UNIFIDECK_TAG = "Unifideck"


class GameMapEntry(NamedTuple):
    r"""One entry in games.map (v3 format).

    Rules:
    - Tab separator because ``=`` can appear in exe paths;
      tabs are never legal in Linux/Windows paths.
    - v1 entries (no tab) and v2 entries (one tab) are still
      valid input — the parser fills ``work_dir`` from
      ``dirname(exe)`` and ``app_id`` from ``0`` respectively.
    - xCloud sentinel: ``exe="xcloud"`` + URL in ``work_dir``
      signals the streaming trigger to the dispatcher.
    - ``app_id`` is the signed 32-bit value Steam stores in
      ``shortcuts.vdf``; ``0`` means "not yet backfilled".
    """
    exe: str
    work_dir: str
    app_id: int = 0


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

    Accepts v1 (``key=exe``), v2 (``key=exe\twork_dir``), and
    v3 (``key=exe\twork_dir\tapp_id``). v1 derives ``work_dir``
    from ``dirname(exe)``; v1 and v2 default ``app_id`` to ``0``
    so the shortcut service can backfill on next save. Malformed
    lines (no ``=``, empty values) and comments / blank lines
    are silently skipped.
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

        if "\t" in value:
            segments = value.split("\t")
            exe = segments[0].strip()
            work_dir = segments[1].strip()
            app_id = 0
            if len(segments) >= 3:
                try:
                    app_id = int(segments[2].strip())
                except ValueError:
                    app_id = 0
            result[key] = GameMapEntry(exe=exe, work_dir=work_dir, app_id=app_id)
        else:
            exe = value.strip()
            work_dir = "" if exe == "xcloud" else str(Path(exe).parent)
            result[key] = GameMapEntry(exe=exe, work_dir=work_dir, app_id=0)

    return result


def format_games_map(mapping: dict[str, GameMapEntry]) -> str:
    r"""Serialize ``{key: GameMapEntry}`` to games.map v3 text.

    Always writes v3 format (``exe\twork_dir\tapp_id``). Sorted
    by key for reproducible output. Entries with ``app_id == 0``
    are written as ``0`` — readers treat that as "unknown, may
    need backfill" rather than a real id.
    """
    lines = [
        "# Unifideck non-Steam shortcut manifest (games.map)",
        "# Format: store:game_id=exe_path\\twork_dir\\tapp_id",
        "# DO NOT EDIT manually. Managed by unifideck-decky.",
    ]

    for key in sorted(mapping.keys()):
        entry = mapping[key]
        lines.append(
            f"{key}={entry.exe}\t{entry.work_dir}\t{entry.app_id}",
        )

    return "\n".join(lines) + "\n"
