"""Wine drive mapping for manual installs.

py_modules/unifideck/stores/manual/drive.py

A manual game's files must live OUTSIDE its Proton prefix (so the
prefix stays disposable — regenerate it, switch Proton, delete it and
the installed game survives). Wine exposes extra drives as symlinks in
``<prefix>/dosdevices``, so we map drive ``D:`` to the game's install
directory (``~/Games/Manual/<game_id>``): inside the installer wizard
the user just picks ``D:\\`` and the files land on the Deck's real
filesystem.

``ensure_manual_drive`` is idempotent and is re-run on every launch —
that is what makes the mapping survive a prefix regeneration.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MANUAL_DRIVE_LETTER = "d"


def ensure_manual_drive(prefix_root: str | Path, target_dir: str | Path) -> bool:
    """Point ``<prefix>/dosdevices/d:`` at ``target_dir``.

    Returns True when the link is in place (created, repointed, or
    already correct). A pre-existing REAL directory at ``d:`` is left
    alone (never destroy data to win a drive letter) and reported as
    False.
    """
    from unifideck.launcher.proton.infrastructure.prefix_layout import (
        resolve_registry_prefix,
    )

    target = Path(target_dir).expanduser()
    if not target.is_dir():
        logger.warning(
            "[ManualDrive] install dir %s missing — not mapping D:", target,
        )
        return False

    dosdevices = resolve_registry_prefix(Path(prefix_root)) / "dosdevices"
    try:
        dosdevices.mkdir(parents=True, exist_ok=True)
        link = dosdevices / f"{MANUAL_DRIVE_LETTER}:"
        if link.is_symlink():
            if link.resolve() == target.resolve():
                return True
            # A manual game's prefix is exclusively ours, so a stale
            # link (e.g. the games dir moved) is safe to repoint.
            link.unlink()
        elif link.exists():
            logger.warning(
                "[ManualDrive] %s exists and is not a symlink — leaving it",
                link,
            )
            return False
        link.symlink_to(target)
    except OSError as e:
        logger.warning("[ManualDrive] could not map D: in %s: %s", prefix_root, e)
        return False
    logger.info("[ManualDrive] %s → %s", link, target)
    return True
