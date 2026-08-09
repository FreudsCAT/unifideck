"""Battle.net prefix lifecycle: auth prefix, template, per-game clones.

py_modules/unifideck/stores/battlenet/prefix/manager.py

Three tiers, following the Ubisoft model because Unifideck does not share
prefixes between games::

    .bnet-auth      the user signs into the client here, once
    .template       pristine, PRE-WARMED, no games
    <uid>           one per game, cloned from .template

**The template must be pre-warmed before it is frozen.** This is not
theoretical: on 2026-08-09 a freshly installed client self-updated from
2.52.3.17554 to 2.52.8.17651 within five minutes of first launch, wrote a
new sibling version folder, and then raised a modal reading *"You need to
restart the application to finish installing a required update."* Cloning a
stale template means every game prefix hits that dialog on first launch —
and nobody can click it in Gaming Mode. So the template is built as:
install client -> launch once -> let it self-update -> restart -> verify ->
only then freeze.

Ownership of any prefix is proven by its in-directory marker, never by its
path. Deleting a prefix here destroys the game inside it.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from unifideck.stores.battlenet import paths
from unifideck.stores.shared.prefix_clone import (
    clone_template,
    ensure_pfx_symlink,
    is_owned_by,
    read_marker,
    repair_from_template,
)

logger = logging.getLogger(__name__)

STORE_ID = "battlenet"
MARKER_FILENAME = paths.PREFIX_MARKER

# Written into the template once it has been launched, self-updated and
# verified. A template without this is NOT safe to clone.
WARMED_MARKER = ".unifideck_battlenet_warmed"


@dataclass(frozen=True, slots=True)
class PrefixStatus:
    """What we know about one prefix on disk."""

    path: Path
    exists: bool
    has_client: bool
    is_ours: bool
    warmed: bool = False

    @property
    def usable(self) -> bool:
        return self.exists and self.has_client


def inspect_prefix(prefix: Path) -> PrefixStatus:
    """Describe a prefix without modifying it."""
    path = Path(prefix)
    return PrefixStatus(
        path=path,
        exists=path.is_dir(),
        has_client=paths.client_installed(path),
        is_ours=is_owned_by(path, MARKER_FILENAME, STORE_ID),
        warmed=(path / WARMED_MARKER).exists(),
    )


class BattlenetPrefixManager:
    """Creates and repairs the three prefix tiers."""

    def __init__(self, prefixes_dir: Path) -> None:
        self._root = Path(prefixes_dir)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def auth_prefix(self) -> Path:
        return paths.auth_prefix(self._root)

    @property
    def template_prefix(self) -> Path:
        return paths.template_prefix(self._root)

    def game_prefix(self, uid: str) -> Path:
        """Default location for a new game prefix.

        Only for creation. An existing prefix is looked up in the id map,
        never rebuilt from the uid — a reconstructed path once stamped a
        marker into a directory no launch opened, wedging the prefix in a
        permanent reset loop.
        """
        return paths.game_prefix(self._root, uid)

    # -- template ----------------------------------------------------------

    def template_status(self) -> PrefixStatus:
        return inspect_prefix(self.template_prefix)

    def mark_template_warmed(self, *, client_build: str | None = None) -> bool:
        """Record that the template has been launched and self-updated.

        Called only after the client has run once, applied its update and
        come back healthy. Until then the template must not be cloned.
        """
        marker = self.template_prefix / WARMED_MARKER
        try:
            marker.write_text(client_build or "", encoding="utf-8")
        except OSError as exc:
            logger.warning("[Battlenet] cannot mark template warmed: %s", exc)
            return False
        logger.info("[Battlenet] template marked warmed (build=%s)", client_build)
        return True

    def template_ready(self) -> bool:
        """True when the template can safely be cloned."""
        status = self.template_status()
        return status.usable and status.warmed

    # -- per-game prefixes -------------------------------------------------

    async def create_game_prefix(self, uid: str, destination: Path | None = None) -> Path | None:
        """Clone the warmed template into a new per-game prefix."""
        if not self.template_ready():
            logger.error(
                "[Battlenet] refusing to clone: template is not warmed "
                "(a stale client would stall every install behind an update modal)",
            )
            return None
        target = Path(destination) if destination else self.game_prefix(uid)
        if target.exists():
            logger.info("[Battlenet] prefix already exists for %s: %s", uid, target)
            return target

        build = self._template_build()
        if not await clone_template(
            self.template_prefix,
            target,
            store=STORE_ID,
            marker_filename=MARKER_FILENAME,
            client_build=build,
        ):
            return None
        ensure_pfx_symlink(target)
        logger.info("[Battlenet] created prefix for %s at %s", uid, target)
        return target

    async def repair_game_prefix(self, prefix: Path) -> bool:
        """Refresh a game prefix's identity, keeping its installed game.

        Additive only. The game lives inside this prefix, so the games
        directory is excluded and nothing is deleted.
        """
        status = inspect_prefix(prefix)
        if not status.exists:
            return False
        if not status.is_ours:
            logger.warning(
                "[Battlenet] refusing to repair unmarked prefix %s "
                "(not provably ours)", prefix,
            )
            return False
        ok = await repair_from_template(self.template_prefix, Path(prefix))
        if ok:
            ensure_pfx_symlink(Path(prefix))
        return ok

    def remove_game_prefix(self, prefix: Path) -> bool:
        """Delete a prefix we created. Refuses anything unmarked.

        This deletes the game too — the install lives inside. Callers must
        have explicit user intent, and the marker check is the backstop.
        """
        path = Path(prefix)
        if not path.is_dir():
            return True
        if not is_owned_by(path, MARKER_FILENAME, STORE_ID):
            logger.error(
                "[Battlenet] refusing to delete unmarked prefix %s — "
                "no proof we created it", path,
            )
            return False
        try:
            shutil.rmtree(path)
        except OSError as exc:
            logger.warning("[Battlenet] cannot remove %s: %s", path, exc)
            return False
        return True

    # -- internals ---------------------------------------------------------

    def _template_build(self) -> str | None:
        marker = read_marker(self.template_prefix, MARKER_FILENAME)
        if marker and marker.client_build:
            return marker.client_build
        versions = paths.client_version_dirs(self.template_prefix)
        return versions[-1].name.rsplit(".", 1)[-1] if versions else None
