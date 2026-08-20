"""Wine <-> Linux path conversion.

py_modules/unifideck/stores/shared/wine_path.py

Pure functions converting Wine-style paths (``C:\\...``) to Linux-side
paths (``<prefix>/drive_c/...``). Every wrapper store needs this, because
the vendor client records install locations in Wine syntax and we have to
reach them from the Linux side:

  * Ubisoft reads them out of UPC config files,
  * Battle.net reads them out of ``product.db`` — a real Hearthstone
    install reported ``C:/Program Files (x86)/Hearthstone``.

Moved here from ``stores/ubisoft/library/wine_path.py`` when Battle.net
became the second consumer; the behaviour is unchanged.

The functions are conservative: they refuse to convert paths that don't
look Wine-formatted, and non-``C:``/``Z:`` drives resolve only through a
real ``dosdevices`` symlink rather than being guessed. Both prefix layouts
are probed, because umu creates ``pfx -> .`` as a self-symlink and
``<prefix>/drive_c`` and ``<prefix>/pfx/drive_c`` can be the same
directory.
"""

from __future__ import annotations

from pathlib import Path


def wine_path_to_linux(wine_path: str, prefix_path: str) -> str | None:
    """Convert a Wine path to its Linux equivalent, or None if not one."""
    path = wine_path.replace("\\", "/")
    if len(path) < 2 or path[1] != ":":
        return None
    drive_letter = path[0].upper()
    relative = path[2:].lstrip("/")
    if drive_letter == "Z":
        return _resolve_z_drive(relative)
    if drive_letter == "C":
        return _resolve_c_drive(prefix_path, relative)
    return _resolve_other_drive(prefix_path, drive_letter, relative)


def _resolve_z_drive(relative: str) -> str:
    """Z: is the Wine view of the host filesystem root."""
    return "/" + relative if relative else "/"


def _resolve_c_drive(prefix_path: str, relative: str) -> str:
    """C: lives under the prefix. Probe both layouts, prefer what exists."""
    prefix = Path(prefix_path)
    for base in (prefix / "pfx", prefix):
        candidate = base / "drive_c" / relative
        if candidate.exists():
            return str(candidate)
    return str(prefix / "pfx" / "drive_c" / relative)


def _resolve_other_drive(
    prefix_path: str,
    drive_letter: str,
    relative: str,
) -> str | None:
    """Other drives resolve only through a real dosdevices symlink.

    Returning None rather than guessing matters: an SD-card install is
    mapped this way, and inventing a path would point install detection at
    somewhere the game is not.
    """
    drive_name = f"{drive_letter.lower()}:"
    prefix = Path(prefix_path)
    for base in (prefix / "pfx", prefix):
        link_path = base / "dosdevices" / drive_name
        if link_path.is_symlink():
            target = str(link_path.resolve())
            return str(Path(target) / relative) if relative else target
    return None
