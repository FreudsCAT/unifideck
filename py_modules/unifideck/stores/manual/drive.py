"""Wine drive mapping for manual installs.

py_modules/unifideck/stores/manual/drive.py

A manual game's files must live OUTSIDE its Proton prefix (so the
prefix stays disposable — regenerate it, switch Proton, delete it and
the installed game survives). Wine exposes extra drives as symlinks in
``<prefix>/dosdevices``, so we map drive ``U:`` to the game's install
directory (``~/Games/Manual/<game_id>``): inside the installer wizard
the user just picks ``U:\\`` and the files land on the Deck's real
filesystem.

``ensure_manual_drive`` is idempotent and is re-run on every launch —
that is what makes the mapping survive a prefix regeneration.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# U: ("Unifideck"). NOT D:: Wine's mountmgr auto-assigns dynamic
# removable devices upward from D (on a Steam Machine the SD-card
# reader is /dev/sda → mountmgr claims d:: and DELETES a foreign d:
# symlink at boot). Proton uses s:/t:, GE-Proton adds v:/x:, z: is /.
# U is out of everyone's reach.
MANUAL_DRIVE_LETTER = "u"
_LEGACY_DRIVE_LETTERS = ("d",)


def ensure_manual_drive(prefix_root: str | Path, target_dir: str | Path) -> bool:
    """Point ``<prefix>/dosdevices/u:`` at ``target_dir``.

    Returns True when the link is in place (created, repointed, or
    already correct). A pre-existing REAL directory at ``u:`` is left
    alone (never destroy data to win a drive letter) and reported as
    False.
    """
    from unifideck.launcher.proton.infrastructure.prefix_layout import (
        resolve_registry_prefix,
    )

    target = Path(target_dir).expanduser()
    if not target.is_dir():
        logger.warning(
            "[ManualDrive] install dir %s missing — not mapping U:", target,
        )
        return False

    # umu prefixes ARE the compat-data root: umu's setup_pfx creates
    # "pfx" as a symlink to ".", so <root>/dosdevices and
    # <root>/pfx/dosdevices are the same physical directory and the
    # root-level user.reg is the real registry. resolve_registry_prefix
    # therefore lands on the dosdevices Wine actually enumerates.
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
        _drop_legacy_links(dosdevices, target)
    except OSError as e:
        logger.warning(
            "[ManualDrive] could not map %s: in %s: %s",
            MANUAL_DRIVE_LETTER.upper(), prefix_root, e,
        )
        return False
    logger.info("[ManualDrive] %s → %s", link, target)
    return True


def _drop_legacy_links(dosdevices: Path, target: Path) -> None:
    """Remove old-letter symlinks earlier builds pointed at ``target``.

    Only a symlink resolving to OUR install dir is removed — Wine's own
    device links (``d::``) and anything else stay untouched.
    """
    for letter in _LEGACY_DRIVE_LETTERS:
        old = dosdevices / f"{letter}:"
        try:
            if old.is_symlink() and old.resolve() == target.resolve():
                old.unlink()
                logger.info("[ManualDrive] dropped legacy %s", old)
        except OSError as e:
            logger.warning("[ManualDrive] legacy cleanup %s: %s", old, e)
