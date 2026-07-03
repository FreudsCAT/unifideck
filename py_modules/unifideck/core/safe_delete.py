"""Shared safe-deletion helpers for uninstall / cleanup flows.

OP-58 | py_modules/unifideck/core/safe_delete.py

Centralises the "is this path safe to ``rmtree``?" guard that the per-store
uninstallers (Epic, GOG, Amazon, Ubisoft) and the global "Delete all data"
cleanup each used to re-implement (some only checked ``/`` and ``$HOME``,
others a loose substring allowlist). One guard means custom install locations
(SD card, ``/mnt`` libraries, user-picked folders) delete reliably while
system paths stay protected.

All functions are synchronous and do blocking I/O — call them from a thread
(``asyncio.to_thread``) on the event loop.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# A path must have at least this many ``parts`` (``/`` counts as one) to be
# eligible for deletion. ``/home/deck/X`` has 4 → allowed; ``/home/deck`` has
# 3 → rejected. Mirrors Ubisoft's existing ``_DELETE_MIN_PATH_DEPTH`` guard.
_MIN_DEPTH = 4


def is_safe_to_delete(path: str | Path) -> bool:
    """True iff *path* is safe to recursively delete.

    Rejects empty paths, ``/``, ``$HOME`` and any ancestor of ``$HOME``
    (e.g. ``/home``), and anything shallower than :data:`_MIN_DEPTH`. Symlinks
    are resolved first so ``~/foo -> /`` can't slip a dangerous target through.
    """
    if not path:
        return False
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        logger.exception("[safe_delete] resolve(%s) failed", path)
        return False
    home = Path.home().resolve()
    if resolved == Path("/") or resolved == home:
        return False
    # Reject ancestors of $HOME (``/``, ``/home``, ``/home/deck`` → all unsafe).
    if home == resolved or _is_ancestor(resolved, home):
        return False
    return len(resolved.parts) >= _MIN_DEPTH


def _is_ancestor(maybe_ancestor: Path, child: Path) -> bool:
    """True iff *maybe_ancestor* is an ancestor of (or equal to) *child*."""
    try:
        child.relative_to(maybe_ancestor)
        return True
    except ValueError:
        return False


def safe_rmtree(path: str | Path) -> bool:
    """``rmtree`` *path* iff it passes :func:`is_safe_to_delete`.

    Returns True when the path is gone afterwards (already-absent counts as
    success — deletion is idempotent), False if the guard rejected it or the
    directory still exists after the attempt.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return True
    if not is_safe_to_delete(p):
        logger.error("[safe_delete] refusing to delete unsafe path: %s", p)
        return False
    try:
        shutil.rmtree(p, ignore_errors=True)
    except OSError:
        logger.exception("[safe_delete] rmtree(%s) failed", p)
    gone = not p.exists()
    if not gone:
        logger.warning("[safe_delete] %s still present after rmtree", p)
    return gone


def canonical_prefix(game_id: str) -> Path:
    """Per-game Proton prefix path for non-Ubisoft stores.

    Matches the launcher's ``_resolve_prefix`` (Epic/GOG/Amazon use a flat
    ``prefixes/<game_id>`` dir — no store subdirectory). Keep in sync with
    ``launcher/proton/infrastructure/core.py``.
    """
    return Path(
        "~/.local/share/unifideck/prefixes",
    ).expanduser() / game_id
