"""Locating the Battle.net client and resolving launch codes.

py_modules/unifideck/launcher/proton/handlers/battlenet_client.py

Runs inside the out-of-process launcher, under the SYSTEM python (3.10 to
3.14), so this is stdlib-only and must not import the plugin backend.

Two things it deliberately does not do:

* **It never combines ``prefix / "drive_c"`` directly.** umu creates
  ``pfx -> .`` as a self-symlink and both layouts occur in the wild; the
  naive combine is what made Ubisoft's recovery path fail to find a
  ``upc.exe`` that was genuinely present.
* **It never derives a FAMILY from a uid by transformation.** The two are
  unrelated namespaces (``fenris`` -> ``Fen``, ``hs_beta`` -> ``WTCG``) and
  Blizzard renames families, so the mapping is read from the id map the
  backend writes. A wrong family fails *silently*.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CLIENT_DIR = "Program Files (x86)/Battle.net"
CLIENT_EXE = "Battle.net.exe"
LAUNCHER_EXE = "Battle.net Launcher.exe"

_DATA_DIR = Path(
    os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")),
) / "unifideck"
ID_MAP_PATH = _DATA_DIR / "battlenet_id_map.json"


def resolve_drive_c(prefix: Path | str) -> Path | None:
    """Resolve a prefix's drive_c across both layouts, or None."""
    root = Path(prefix)
    modern = root / "pfx" / "drive_c"
    if modern.is_dir():
        return modern
    legacy = root / "drive_c"
    return legacy if legacy.is_dir() else None


def _client_dir(prefix: Path | str) -> Path | None:
    drive_c = resolve_drive_c(prefix)
    if drive_c is None:
        return None
    found = drive_c / CLIENT_DIR
    return found if found.is_dir() else None


def find_client_exe(prefix: Path | str) -> Path | None:
    """``Battle.net.exe`` — the binary that accepts ``--exec``.

    Confirmed on-device: ``Battle.net Launcher.exe`` does not.
    """
    parent = _client_dir(prefix)
    if parent is None:
        return None
    exe = parent / CLIENT_EXE
    return exe if exe.is_file() else None


def find_launcher_exe(prefix: Path | str) -> Path | None:
    """``Battle.net Launcher.exe`` — started first, owns the wineserver."""
    parent = _client_dir(prefix)
    if parent is None:
        return None
    exe = parent / LAUNCHER_EXE
    return exe if exe.is_file() else None


def _load_id_map() -> dict[str, dict[str, object]]:
    try:
        data = json.loads(ID_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_family(uid: str) -> str | None:
    """The ``--exec`` family code for a game uid, or None.

    Prefers a family already proven to launch this game: an obsolete code
    fails silently, so one that has demonstrably worked is never
    second-guessed.
    """
    record = _load_id_map().get(uid)
    if not isinstance(record, dict):
        logger.warning("[battlenet] no id-map record for uid=%s", uid)
        return None
    proven = record.get("last_launch_family") if record.get("launch_ok_at") else None
    family = proven or record.get("family")
    if not isinstance(family, str) or not family:
        logger.warning("[battlenet] id-map record for %s has no family", uid)
        return None
    return family


def resolve_prefix(uid: str) -> Path | None:
    """The recorded prefix for a game. Never reconstructed from the uid."""
    record = _load_id_map().get(uid)
    if not isinstance(record, dict):
        return None
    path = record.get("prefix_path")
    return Path(path) if isinstance(path, str) and path else None


def client_installed(prefix: Path | str) -> bool:
    return find_client_exe(prefix) is not None
