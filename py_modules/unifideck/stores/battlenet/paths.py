"""Prefix and file-path resolution for the Battle.net store.

py_modules/unifideck/stores/battlenet/paths.py

Unifideck gives every game its own prefix — the deliberate difference from
NonSteamLaunchers, where one shared prefix means one bad client update
takes out the whole library. Battle.net therefore uses the Ubisoft
three-tier layout::

    <prefixes_dir>/.bnet-auth      the user signs into the client here, once
    <prefixes_dir>/.template       pristine, pre-warmed, no games
    <prefixes_dir>/<uid>           one per game, cloned from .template

Two on-device findings shape this module:

* **umu rewrites ``WINEPREFIX`` to ``<prefix>/pfx/`` and creates ``pfx -> .``
  as a self-symlink**, so ``<prefix>/drive_c`` and ``<prefix>/pfx/drive_c``
  are the same directory. Everything here goes through
  ``resolve_drive_c``; combining ``prefix / "drive_c"`` naively is the bug
  that made ``ubisoft_recovery`` fail to find a genuinely present exe.
* A per-game prefix path is **recorded, never reconstructed** from the game
  id. ``prefix_setup.py`` documents a Ubisoft incident where a
  reconstructed path stamped a marker into a directory no launch ever
  opened, causing a permanent reset loop.
"""

from __future__ import annotations

from pathlib import Path

from unifideck.launcher.proton.infrastructure.prefix_layout import resolve_drive_c

# Prefix directory names. Dot-prefixed so a game uid can never collide.
AUTH_PREFIX_NAME = ".bnet-auth"
TEMPLATE_PREFIX_NAME = ".template"

# Client layout inside a prefix's drive_c.
CLIENT_DIR = "Program Files (x86)/Battle.net"
# Takes --exec. Confirmed on-device: the Launcher does not.
CLIENT_EXE = "Battle.net.exe"
# Owns the wineserver session; started first in a two-phase launch.
LAUNCHER_EXE = "Battle.net Launcher.exe"
CLIENT_CONFIG = "users/steamuser/AppData/Roaming/Battle.net/Battle.net.config"

# Marker written into a prefix we built, so ownership is provable rather
# than inferred from the path (appid inference nearly deleted 1 GB of user
# prefixes once already).
PREFIX_MARKER = ".unifideck_battlenet"


def auth_prefix(prefixes_dir: Path) -> Path:
    return Path(prefixes_dir) / AUTH_PREFIX_NAME


def template_prefix(prefixes_dir: Path) -> Path:
    return Path(prefixes_dir) / TEMPLATE_PREFIX_NAME


def game_prefix(prefixes_dir: Path, uid: str) -> Path:
    """Default per-game prefix path.

    Only for *creating* a prefix. To find an existing one, read the
    recorded path from the id map — never rebuild it from the uid.
    """
    return Path(prefixes_dir) / uid


def drive_c(prefix: Path) -> Path | None:
    """Resolve a prefix's drive_c across both layouts, or None."""
    return resolve_drive_c(Path(prefix))


def client_dir(prefix: Path) -> Path | None:
    dc = drive_c(prefix)
    if dc is None:
        return None
    found = dc / CLIENT_DIR
    return found if found.is_dir() else None


def client_exe(prefix: Path) -> Path | None:
    """``Battle.net.exe`` — the binary that accepts ``--exec``."""
    parent = client_dir(prefix)
    if parent is None:
        return None
    exe = parent / CLIENT_EXE
    return exe if exe.is_file() else None


def launcher_exe(prefix: Path) -> Path | None:
    """``Battle.net Launcher.exe`` — spawned first, owns the wineserver."""
    parent = client_dir(prefix)
    if parent is None:
        return None
    exe = parent / LAUNCHER_EXE
    return exe if exe.is_file() else None


def client_config(prefix: Path) -> Path | None:
    dc = drive_c(prefix)
    if dc is None:
        return None
    cfg = dc / CLIENT_CONFIG
    return cfg if cfg.is_file() else None


def client_installed(prefix: Path) -> bool:
    """True when a prefix actually holds a usable client."""
    return client_exe(prefix) is not None


def is_ours(prefix: Path) -> bool:
    """True only when the in-directory marker proves we built this prefix.

    Never infer ownership from the path. A prefix under our directory that
    lacks the marker is treated as not ours, because deleting a user's
    prefix is unrecoverable and the marker is cheap.
    """
    return (Path(prefix) / PREFIX_MARKER).exists()


def client_version_dirs(prefix: Path) -> list[Path]:
    """Sibling versioned client folders, newest last.

    The client self-updates into a new sibling (``Battle.net.17651``
    appeared beside ``Battle.net.17554`` within five minutes of first
    launch), so repair means removing the newest and letting the
    known-good one run.
    """
    parent = client_dir(prefix)
    if parent is None:
        return []
    dirs = [p for p in parent.glob("Battle.net.*") if p.is_dir()]

    def _build(path: Path) -> tuple[int, str]:
        suffix = path.name.rsplit(".", 1)[-1]
        return (int(suffix), path.name) if suffix.isdigit() else (-1, path.name)

    return sorted(dirs, key=_build)
