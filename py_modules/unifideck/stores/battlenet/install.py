"""Battle.net install orchestration — prefix placement, not downloading.

py_modules/unifideck/stores/battlenet/install.py

Nothing here downloads a game. Battle.net is a wrapper store: the vendor
client inside the prefix owns the download, and the install is a user click
in its window. What this module owns is everything that has to be true
*before* that click, which is essentially one thing — **the prefix is in the
right place**.

That matters more than it sounds. The game installs inside the prefix, so
the prefix's location is the game's location, and Wine derives ``C:``'s free
space from the filesystem under ``drive_c``. Battle.net originally ignored
the storage location the user picked, and the symptom was not a game in the
wrong folder: the client refused an 83 GB install for lack of space, quoting
the 45 GB internal drive, while the SD card the user had chosen had 164 GB
free.

Split out of ``store.py`` when that file hit its size cap. The seam is the
one Ubisoft already uses (``stores/ubisoft/installer/``), and the placement
policy itself is shared by both stores in
``stores/shared/prefix_placement``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from unifideck.core.types.results import InstallResult
from unifideck.stores.shared.prefix_placement import (
    cleanup_abandoned_prefix,
    reset_for_fresh_install,
    resolve_prefix_target,
)

from . import library as library_mod
from . import paths
from .ownership import read_catalog

if TYPE_CHECKING:
    from .id_map import BattlenetIdMap
    from .prefix import BattlenetPrefixManager

logger = logging.getLogger(__name__)

STORE_ID = "battlenet"
LABEL = "Battlenet"


def holds_ready_install(prefix: Path) -> bool:
    """Whether a prefix's client state reports at least one ready install.

    Synchronous so it can be handed straight to a worker thread, and the
    only thing standing between a failed install and a deleted game —
    ``product.db`` plus ``aggregate.json`` are what the client itself
    consults, so this is the strongest evidence available.
    """
    drive_c = paths.drive_c(prefix)
    if drive_c is None:
        return False
    state = library_mod.read_install_state(drive_c, prefix)
    return any(game.is_ready for game in state.values())


def _failed(game_id: str, error: str, code: str) -> InstallResult:
    return InstallResult(
        success=False,
        game_id=game_id,
        store=STORE_ID,
        error=error,
        error_code=code,
    )


class BattlenetInstaller:
    """Prepares a game's prefix and hands the download to the client."""

    def __init__(
        self,
        prefixes: BattlenetPrefixManager,
        id_map: BattlenetIdMap,
    ) -> None:
        self._prefixes = prefixes
        self._id_map = id_map

    async def install(
        self, game_id: str, install_path: str | None = None,
    ) -> InstallResult:
        """Place the prefix so the client can install into it.

        ``--exec="install <FAMILY>"`` does **not** start a download — that
        was measured against the current client with a known-good family
        code. The install is a user click inside the client, exactly as it
        is for Ubisoft, so this prepares the prefix and the caller signals
        the frontend to bring the client up.
        """
        # The install run opens the client on the game's page via
        # ``--exec="launch <FAMILY>"``, so a missing family fails the install
        # the same way it fails a launch — and silently. Resolve it here
        # rather than letting the launcher discover the gap.
        if not await self.ensure_family(game_id):
            return _failed(
                game_id,
                "Unifideck doesn't know Battle.net's code for this game — "
                "re-sync your library",
                "family_unknown",
            )
        if not self._prefixes.auth_ready():
            return _failed(
                game_id,
                "Sign in to Battle.net first — the game prefix inherits "
                "your signed-in session",
                "not_signed_in",
            )
        target = await self._place_prefix(game_id, install_path)
        prefix = await self._prefixes.create_game_prefix(game_id, target)
        if prefix is None:
            await self.cleanup_abandoned(game_id, target)
            return _failed(
                game_id,
                "Could not prepare the game's Battle.net prefix — close "
                "the Battle.net window and try again",
                "prefix_clone_failed",
            )
        return InstallResult(
            success=True,
            game_id=game_id,
            store=STORE_ID,
            install_path=str(prefix),
            metadata={"phase": "manual", "prefix": str(prefix)},
        )

    async def _place_prefix(
        self, game_id: str, install_path: str | None,
    ) -> Path:
        """Resolve where this game's prefix goes and clear the way for it."""
        target = resolve_prefix_target(
            STORE_ID, game_id, install_path,
            self._prefixes.game_prefix(game_id),
        )
        # Every Install rebuilds the prefix, so clear both the previously
        # recorded location (which differs once the user picks a new disk)
        # and the target itself.
        await reset_for_fresh_install(
            self._id_map.resolve_prefix(game_id),
            target,
            self._prefixes.remove_game_prefix,
            label=LABEL,
        )
        # Record BEFORE the clone: the launcher, install detection and
        # uninstall all read this path back rather than rebuilding it, so an
        # interrupted rsync leaves a reachable prefix, not an invisible
        # orphan.
        self._id_map.merge(game_id, prefix_path=str(target))
        return target

    async def ensure_family(self, game_id: str) -> bool:
        """True once a family code for ``game_id`` is in the id map.

        Normally already there — ``get_library`` records the whole library on
        every sync. This covers the install-without-a-recent-sync case by
        re-reading the catalog for just this title, which costs one catalog
        parse rather than a full library build.
        """
        if self._id_map.resolve_family(game_id):
            return True
        drive_c = paths.drive_c(self._prefixes.auth_prefix)
        if drive_c is None:
            return False
        catalog = await asyncio.to_thread(read_catalog, drive_c)
        family = library_mod.family_from_catalog(catalog, game_id)
        if family is None:
            logger.error("[Battlenet] no family code in the catalog for %s", game_id)
            return False
        self._id_map.merge(game_id, family=family)
        logger.info(
            "[Battlenet] resolved family %s for %s at install time", family, game_id,
        )
        return True

    async def cleanup_abandoned(self, game_id: str, prefix: Path) -> None:
        """Drop the prefix left by an install that produced no game.

        An interrupted clone to removable media would otherwise leave a
        partial ~1.2 GB tree squatting on the disk the user picked, with the
        id map still pointing at it.
        """
        deleted = await cleanup_abandoned_prefix(
            prefix,
            recorded=self._id_map.resolve_prefix(game_id),
            holds_game=self._holds_game,
            remover=self._prefixes.remove_game_prefix,
            label=LABEL,
        )
        if deleted:
            self._id_map.clear_prefix(game_id)

    async def _holds_game(self, prefix: Path) -> bool:
        return await asyncio.to_thread(holds_ready_install, Path(prefix))
