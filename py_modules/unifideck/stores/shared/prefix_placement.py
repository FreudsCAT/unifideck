"""Where a wrapper store's per-game prefix goes, and when it may be removed.

py_modules/unifideck/stores/shared/prefix_placement.py

For a **wrapper store** the vendor's own Windows client runs inside the
prefix and installs the game *inside* it — ``launcher/wrapper_stores.py``
states that property as :func:`prefix_owns_game_install`. Three consequences
follow from it, and this module is the single place each is decided:

1. **Placement.** Putting the prefix somewhere *is* putting the game there,
   so the storage location the user picked must become the prefix root. It
   is also the only way the vendor client's own free-space check reads the
   right volume: Battle.net shipped without this and its installer refused
   an 83 GB download citing the 45 GB internal drive, while the SD card the
   user had actually picked had 164 GB free.
2. **A fresh start.** Install rebuilds the prefix, so any prior one — at the
   old recorded location *or* at the new target — is cleared first.
3. **Abandoned cleanup.** An install that produced no game must not leave a
   prefix squatting on the user's chosen disk.

Ubisoft solved all three privately and Battle.net had none of them. Sharing
rather than copying is not tidiness here: ``wrapper_stores`` exists because
the same question asked as a bare ``store == "ubisoft"`` in five places is
how the 2026-08-01 incident deleted Rayman Origins. EA App is next and
inherits this by being added to ``_PREFIX_OWNS_INSTALL`` — a row, not a
function.

Deliberately **not** here: the deletion itself. Ubisoft deletes through the
uninstall pipeline (protected paths + depth check), Battle.net through the
``.unifideck_battlenet`` marker check that proves we created the directory.
Both backstops were earned by incidents in which a prefix holding a real
game was destroyed, so each store keeps its own and passes it in as
``remover``. The same applies to ``holds_game``: the evidence that a prefix
contains a game is store-specific, and a generic guess would be the exact
mistake these guards exist to prevent.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from unifideck.launcher.wrapper_stores import prefix_owns_game_install

logger = logging.getLogger(__name__)

# All three take an absolute prefix path and may be sync or async, so a store
# can pass an existing method without wrapping it.
Remover = Callable[[Path], Awaitable[bool] | bool]
HoldsGame = Callable[[Path], Awaitable[bool] | bool]
BeforeRemove = Callable[[Path], Awaitable[bool] | bool]

# The directory a relocated prefix lives under, inside the picked base.
PREFIXES_DIR_NAME = "prefixes"


async def _maybe_await(value: Awaitable[bool] | bool) -> bool:
    """Resolve a callback that may be sync or async."""
    if inspect.isawaitable(value):
        return bool(await value)
    return bool(value)


def prefix_path_for_base(base: str | Path, store: str, game_id: str) -> Path:
    """Per-game Wine-prefix path under a user-picked storage base.

    Mirrors the internal layout (``prefixes/<store>/<game_id>``) so the
    on-disk shape is the same wherever the prefix lives, and the directory
    name stays the id that detection and enumeration key on.
    """
    return Path(base).expanduser() / PREFIXES_DIR_NAME / store / game_id


def resolve_prefix_target(
    store: str,
    game_id: str,
    install_path: str | None,
    default: str | Path,
) -> Path:
    """The prefix root for an install, honouring the user's storage pick.

    Only relocates when the store's games live inside the prefix. For every
    other store the game downloads outside the prefix, so the pick is a
    plain install directory and moving the prefix would achieve nothing.
    """
    if install_path and prefix_owns_game_install(store):
        return prefix_path_for_base(install_path, store, game_id)
    return Path(default)


async def _remove_one(
    path: Path, remover: Remover, label: str, reason: str,
) -> bool:
    """Run a store's remover, converting any failure into ``False``.

    Best-effort by design: a prefix that could not be deleted still lets the
    caller clone over the top, which is a better outcome than aborting the
    install the user asked for.
    """
    try:
        removed = await _maybe_await(remover(path))
    except Exception:
        logger.exception("[%s] %s: could not remove %s", label, reason, path)
        return False
    logger.info(
        "[%s] %s: %s %s",
        label, reason, "removed" if removed else "kept", path,
    )
    return removed


async def reset_for_fresh_install(
    old: str | Path | None,
    new: str | Path,
    remover: Remover,
    *,
    label: str,
    before_remove: BeforeRemove | None = None,
) -> None:
    """Delete any pre-existing per-game prefix so Install starts clean.

    Covers both the previously recorded location — an orphan from a prior
    install to a different disk, or a leftover from a prior uninstall — and
    the resolved target, deduped so the common case does one pass.

    ``before_remove`` runs on each prefix while it still exists, and exists
    for one reason: these prefixes hold the vendor client's signed-in session,
    the vendor rotates its token on every run, and the prefix about to be
    deleted usually holds a *newer* session than the store's auth prefix.
    Deleting it without capturing that first strands auth on a server-stale
    token, and the reported symptom is a login prompt on the very next
    install. Failures are swallowed: a capture is an optimisation, and it must
    never block the install the user asked for.
    """
    seen: set[Path] = set()
    for candidate in (old, new):
        if candidate is None:
            continue
        path = Path(candidate)
        if path in seen:
            continue
        seen.add(path)
        if not await asyncio.to_thread(path.is_dir):
            continue
        if before_remove is not None:
            try:
                await _maybe_await(before_remove(path))
            except Exception:
                logger.exception(
                    "[%s] pre-removal hook failed for %s", label, path,
                )
        await _remove_one(path, remover, label, "fresh-install reset")


async def cleanup_abandoned_prefix(
    prefix: str | Path,
    *,
    recorded: str | Path | None,
    holds_game: HoldsGame,
    remover: Remover,
    label: str,
) -> bool:
    """Remove the prefix left behind by an install that produced no game.

    Returns True only when the prefix was actually deleted, so the caller
    knows whether to clear its recorded path.

    Two gates, both load-bearing:

    * **Only a recorded location is cleaned.** That is the user-picked
      placement; the shared internal default is left alone for reuse.
    * **A prefix that holds a game is never touched**, however the install
      ended. The install detectors these stores rely on can false-negative,
      so ``holds_game`` is the store's own strongest available test and a
      True answer always wins.
    """
    if not recorded:
        return False
    path = Path(prefix)
    # No existence check on purpose. A prefix that was never created still
    # leaves a recorded path behind, and the caller clears that only when
    # this returns True — every remover here treats an absent directory as
    # already removed, so the dangling record gets cleaned up too.
    if await _maybe_await(holds_game(path)):
        logger.info(
            "[%s] abandoned install, but the prefix holds a game — keeping %s",
            label, path,
        )
        return False
    return await _remove_one(path, remover, label, "abandoned prefix")
