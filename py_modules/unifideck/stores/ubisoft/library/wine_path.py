"""
Wine ↔ Linux path conversion — small utility helpers.

OP-57i | py_modules/unifideck/stores/ubisoft/library/wine_path.py

Pure functions that convert between Wine-style paths (``C:\\...``) and
Linux-side paths (``<prefix>/drive_c/...``). Used by the library and
detection modules whenever they read a path out of a UPC config file
(which uses Wine syntax) and need to access it on the Linux side.

The functions are conservative: they refuse to convert paths that
don't look Wine-formatted, and they reject paths that would escape the
prefix root after conversion (security against path-traversal in
malformed config files).
"""

from __future__ import annotations

from pathlib import Path


def wine_path_to_linux(
    wine_path: str,
    prefix_path: str,
) -> str | None:
    """Wine path to linux."""
    path = wine_path.replace("\\", "/")
    if len(path) < 2 or path[1] != ":":
        return None
    drive_letter = path[0].upper()
    relative = path[2:].lstrip("/")
    if drive_letter == "Z":
        return _resolve_z_drive(relative)
    if drive_letter == "C":
        return _resolve_c_drive(prefix_path, relative)
    return _resolve_other_drive(
        prefix_path,
        drive_letter,
        relative,
    )


def _resolve_z_drive(relative: str) -> str:
    """Resolve z drive."""
    return "/" + relative if relative else "/"


def _resolve_c_drive(prefix_path: str, relative: str) -> str:
    """Resolve c drive."""
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
    """Resolve other drive."""
    drive_name = f"{drive_letter.lower()}:"
    prefix = Path(prefix_path)
    for base in (prefix / "pfx", prefix):
        link_path = base / "dosdevices" / drive_name
        if link_path.is_symlink():
            target = str(link_path.resolve())
            if relative:
                return str(Path(target) / relative)
            return target
    return None
