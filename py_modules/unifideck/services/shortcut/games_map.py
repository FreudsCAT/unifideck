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


def generate_app_id(launcher: str, identity: str) -> int:
    """Compute deterministic 32-bit shortcut ID from launcher + identity.

    Matches Steam's non-Steam-shortcut algorithm: CRC32 of the
    composed key with the top bit set, returned as signed
    32-bit. The composed key is ``f"{launcher}|{identity}"``.

    **The ``|`` separator is load-bearing — it must NOT be removed
    or replaced.** This format is byte-identical to v0.6.1's
    ``shortcuts_manager.generate_app_id`` (Release-0.6.1, line
    1211) which is the algorithm every released user's Steam
    library is keyed on. Changing this format silently re-keys
    every existing shortcut, losing Steam playtime, categories,
    hidden flags, and on-disk grid artwork bound to the old
    appid. The pinning test in this module's test file enforces
    the exact byte sequence.

    ``identity`` is the caller-controlled stable component:

    * **Game shortcuts** pass ``f"{store}:{store_game_id}"``.
      Anchoring on the store-scoped pair (not the title) keeps
      the same title on different stores in separate shortcuts —
      avoids the cross-store collision where two stores would
      share one appid and fight over LaunchOptions.

    * **Auth shortcuts** (Ubisoft Connect, Epic Sign-In, etc.)
      pass their constant display name (``"Ubisoft Connect"``,
      ``"Epic Games Sign-In"``, ...). They never collide with
      game shortcuts because no real game uses those strings.
    """
    key = f"{launcher}|{identity}"
    crc = binascii.crc32(key.encode("utf-8")) | 0x80000000
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
        parsed = _parse_map_line(raw_line.strip())
        if parsed is not None:
            key, entry = parsed
            result[key] = entry
    return result


def _parse_map_line(line: str) -> tuple[str, GameMapEntry] | None:
    """Parse one ``key=value`` line; ``None`` for comment/blank/malformed."""
    if not line or line.startswith("#"):
        return None
    parts = line.split("=", 1)
    if len(parts) != 2:
        return None
    key, value = parts
    return key.strip(), _parse_map_value(value)


def _parse_map_value(value: str) -> GameMapEntry:
    r"""Decode a games.map value: v1 (``exe``) or v2/v3 (tab-separated)."""
    if "\t" not in value:
        exe = value.strip()
        work_dir = "" if exe == "xcloud" else str(Path(exe).parent)
        return GameMapEntry(exe=exe, work_dir=work_dir, app_id=0)
    segments = value.split("\t")
    app_id = _parse_app_id(segments[2]) if len(segments) >= 3 else 0
    return GameMapEntry(
        exe=segments[0].strip(),
        work_dir=segments[1].strip(),
        app_id=app_id,
    )


def _parse_app_id(raw: str) -> int:
    """Parse the v3 app_id column; ``0`` (backfill marker) on garbage."""
    try:
        return int(raw.strip())
    except ValueError:
        return 0


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
