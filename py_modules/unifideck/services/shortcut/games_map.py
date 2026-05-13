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
    """One entry in the Unifideck games-map file.

    Attributes:
        exe: absolute path of the game's executable (what Steam
            launches when the user clicks the shortcut).
        work_dir: working directory passed to the subprocess —
            typically the executable's parent, but some games
            require a specific cwd (e.g. relative-path asset
            loading).
    """

    exe: str
    work_dir: str


def generate_app_id(exe: str, title: str) -> int:
    """Compute a deterministic 32-bit signed AppID for a shortcut.

    Steam derives AppIDs for non-Steam shortcuts from a hash of
    the executable path + title (its actual algorithm is
    proprietary; this is a CRC32-based approximation that has
    proven stable in practice). Deterministic means:

    * deleting a shortcut and re-creating it with the same
      (exe, title) yields the same AppID — the user's artwork,
      controller layout and category assignments are preserved;
    * the function is pure: same inputs → same output, always.

    The OR with ``0x80000000`` sets the high bit (Steam's
    convention for non-Steam apps), and the final subtraction
    converts the unsigned 32-bit value into the signed range
    that Python expects.

    Args:
        exe: executable path used as the AppID seed.
        title: display title used as the AppID seed.

    Returns:
        Steam-compatible signed 32-bit AppID.
    """
    key = (exe + title).encode("utf-8")
    crc = zlib.crc32(key) | 0x80000000
    return crc - 0x100000000 if crc >= 0x80000000 else crc


def parse_games_map(content: str) -> dict[str, GameMapEntry]:
    """Parse a games-map file body into a typed dict.

    File format is one ``key=exe[\\tworkdir]`` line per entry,
    with ``#``-prefixed comments and blank lines ignored. When
    the work directory is absent, it defaults to the executable's
    parent directory.

    Lines with no ``=`` separator or with an empty key/value are
    silently skipped — better to load a partially-corrupt file
    than to refuse the whole map.

    Args:
        content: raw text content of the games-map file.

    Returns:
        Mapping ``"<store>:<game_id>" → GameMapEntry``.
    """
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
    """Serialise a games map back to the on-disk text format.

    Entries are sorted by key so the file is stable across
    saves — easier to diff and to spot accidental changes when
    inspecting the file manually.

    Args:
        mapping: ``"<store>:<game_id>" → GameMapEntry`` dict.

    Returns:
        File body (always ends with a single ``\\n``).
    """
    lines = [
        f"{k}={entry.exe}\t{entry.work_dir}" for k, entry in sorted(mapping.items())
    ]
    return "\n".join(lines) + "\n"
