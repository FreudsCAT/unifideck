"""Recognising a Ubisoft Connect install arriving in the prefix.

py_modules/unifideck/stores/ubisoft/installer/manual_ui_poll.py

The watching *loop* — timeouts, the two give-up watchdogs, completion, progress
ticks — used to live here and now lives once, in
:mod:`unifideck.stores.shared.wrapper_install.watch`, shared with Battle.net and
whatever wrapper store comes next. What is left is the part that is genuinely
Ubisoft's: how you tell that UPC has put a game on disk.

UPC gives no authoritative completion signal — nothing on disk says "finished",
and the client stays running in a service-mode background loop afterwards — so
:meth:`UbisoftInstallProbe.is_complete` returns ``None`` and the shared loop
falls back to watching the install directory's size hold steady. Battle.net, by
contrast, reads its client's own ``product.db``. That difference is exactly why
the verdict is three-valued.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from unifideck.stores.shared.installed_size import dir_size_bytes
from unifideck.stores.ubisoft.library.detection_helpers import looks_like_game_install

logger = logging.getLogger(__name__)

STORE_ID = "ubisoft"
CLIENT_LABEL = "Ubisoft Connect"

_UPC_GAMES_REL = str(
    Path("drive_c") / "Program Files (x86)" / "Ubisoft"
    / "Ubisoft Game Launcher" / "games",
)


def upc_game_dirs(prefix_path: str) -> tuple[str, str]:
    """Both spellings of UPC's in-prefix ``games/`` directory.

    umu makes ``pfx`` a self-symlink to the prefix, so the same directory is
    reachable by two paths and which one appears depends on how the prefix was
    created. Watching only one of them missed real installs.
    """
    return (
        str(Path(prefix_path) / _UPC_GAMES_REL),
        str(Path(prefix_path) / "pfx" / _UPC_GAMES_REL),
    )


def _listing(path: str) -> set[str]:
    """Entry names in ``path``; empty when it does not exist yet.

    An absent directory must baseline as EMPTY rather than be skipped. On a
    fresh prefix UPC creates ``games/`` only once the install starts, so the
    old "watch it only if it already exists" rule left it unwatched and the
    newly-installed game was never detected — a false ``no_install_detected``
    for an install that worked fine.
    """
    try:
        return {entry.name for entry in Path(path).iterdir()}
    except OSError:
        return set()


class UbisoftInstallProbe:
    """Detects a UPC install by diffing directory listings.

    Two locations are watched, in priority order: the ``install_base`` we asked
    UPC to use, then UPC's own per-prefix ``games/`` directories — the fallback
    for when UPC overrides the requested path and drops the game in its default
    folder anyway.
    """

    store = STORE_ID
    client_label = CLIENT_LABEL

    def __init__(self, install_base: str, prefix_path: str) -> None:
        self._install_base = install_base
        self._prefix_path = prefix_path

    def snapshot(self) -> dict[str, set[str]]:
        """Baseline every watched directory."""
        watched = (self._install_base, *upc_game_dirs(self._prefix_path))
        return {path: _listing(path) for path in watched}

    def detect(self, baseline: Any) -> str | None:
        """First new directory that looks like a game install, else ``None``.

        ``install_base`` is checked first so the user's chosen location wins
        when UPC honoured it.
        """
        if not isinstance(baseline, dict):
            return None
        ordered = (self._install_base, *upc_game_dirs(self._prefix_path))
        for path in ordered:
            found = self._new_game_dir(path, baseline.get(path, set()))
            if found:
                return found
        return None

    @staticmethod
    def _new_game_dir(base: str, before: set[str]) -> str | None:
        """A directory under ``base`` that is new since ``before`` and is a game."""
        for name in _listing(base) - before:
            candidate = str(Path(base) / name)
            if Path(candidate).is_dir() and looks_like_game_install(candidate):
                return candidate
        return None

    def measure(self, install_dir: str) -> int:
        return dir_size_bytes(install_dir)

    def is_complete(self, install_dir: str) -> bool | None:
        """UPC publishes no completion signal — defer to size stability.

        ``uplay_install.state`` flipping to ``0x0A`` looks like a candidate and
        is not: it is written per *game slot* and the install detector already
        treats it as "a game lives here", not "this download finished".
        """
        del install_dir
        return None
