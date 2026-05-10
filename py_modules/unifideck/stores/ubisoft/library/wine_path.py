"""wine_path.py — Convert Wine-style paths to Linux filesystem paths.

# OP-57i | py_modules/unifideck/stores/ubisoft/library/wine_path.py | Depends: (none)

UPC stores executable paths in the form ``C:\\Program Files (x86)\\…``
or ``Z:\\home\\deck\\…``. To resolve those against the actual filesystem
we need to know which Wine prefix they came from and then map drive
letters to the prefix's drive_c / dosdevices conventions.
"""
from __future__ import annotations

from pathlib import Path


def wine_path_to_linux(wine_path: str, prefix_path: str) -> str | None:
    """Convert ``wine_path`` to a real Linux path within ``prefix_path``.

    Returns ``None`` if the input doesn't look like a Wine path or if
    the corresponding drive can't be located on disk.
    """
    if not wine_path:
        return None
    normalized = wine_path.replace("\\", "/")
    if len(normalized) < 2 or normalized[1] != ":":
        return None
    drive_letter = normalized[0].upper()
    relative = normalized[2:].lstrip("/")
    if drive_letter == "Z":
        return _resolve_z_drive(relative)
    if drive_letter == "C":
        return _resolve_c_drive(prefix_path, relative)
    return _resolve_other_drive(prefix_path, drive_letter, relative)


def _resolve_z_drive(relative: str) -> str:
    """Z: maps to the Linux root."""
    return "/" + relative


def _resolve_c_drive(prefix_path: str, relative: str) -> str:
    """C: maps to ``<prefix>/drive_c`` (or ``<prefix>/pfx/drive_c``)."""
    candidates = (
        Path(prefix_path) / "drive_c" / relative,
        Path(prefix_path) / "pfx" / "drive_c" / relative,
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


def _resolve_other_drive(
    prefix_path: str, drive_letter: str, relative: str,
) -> str | None:
    """Any other letter resolves through the prefix's ``dosdevices`` map."""
    letter = drive_letter.lower()
    candidates = (
        Path(prefix_path) / "dosdevices" / f"{letter}:",
        Path(prefix_path) / "pfx" / "dosdevices" / f"{letter}:",
    )
    for dev in candidates:
        if dev.exists() or dev.is_symlink():
            try:
                target = dev.resolve()
            except OSError:
                continue
            return str(target / relative)
    return None
