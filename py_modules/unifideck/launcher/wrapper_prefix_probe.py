"""Detect whether a wrapper store's title already has a bootstrapped prefix.

py_modules/unifideck/launcher/wrapper_prefix_probe.py

A wrapper store's install has a window where the game is real enough to launch
the vendor client into, and not yet real enough to have a ``games.map`` row:
the prefix has been placed and the client installed into it, but the game
itself has not downloaded. The launcher is asked to open the client during
exactly that window, so without this probe it raises ``GameNotFoundError`` and
exits 2 before the client ever appears — and the install then waits for a
window that will never open.

Both wrapper stores need it, so the store-specific part is a row:

* which id map records the prefix location (recorded, never reconstructed —
  games can live on an SD card), and
* which client executable proves the prefix is bootstrapped rather than an
  empty directory left by an abandoned install.

This was Ubisoft's alone, as ``ubisoft_prefix_probe``. Battle.net got by
without it only by accident: the install used to report success the instant its
prefix was cloned, so the premature ``DOWNLOAD_COMPLETE`` wrote a ``games.map``
row within milliseconds while ``RunGame`` took ~3 s to reach the launcher. Once
the install correctly waited for the game, that row was no longer there and
every Battle.net install died with ``GameNotFoundError: game 'battlenet:osi'
not found in games.map``.

Stdlib-only: this runs under the SYSTEM python (3.10-3.14), not Decky's 3.11.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _ProbeSpec:
    """Per-store: where the prefix is recorded, and what proves it is ready."""

    #: Id-map filename under the Unifideck data dir.
    id_map: str
    #: Client executable, relative to the prefix's ``drive_c``.
    client_rel: str


_SPECS: dict[str, _ProbeSpec] = {
    "ubisoft": _ProbeSpec(
        id_map="ubisoft_id_map.json",
        client_rel="Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe",
    ),
    "battlenet": _ProbeSpec(
        id_map="battlenet_id_map.json",
        client_rel="Program Files (x86)/Battle.net/Battle.net.exe",
    ),
}

# The key every wrapper store's id map records its prefix location under.
_PREFIX_KEY = "prefix_path"


def _data_dir() -> Path:
    """Unifideck's data dir, honouring ``XDG_DATA_HOME``."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "unifideck"


def _recorded_prefix(spec: _ProbeSpec, game_id: str) -> str | None:
    """The prefix path this store recorded for ``game_id``, if any."""
    try:
        data = json.loads((_data_dir() / spec.id_map).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(game_id)
    if not isinstance(entry, dict):
        return None
    recorded = entry.get(_PREFIX_KEY)
    return recorded if isinstance(recorded, str) and recorded else None


def wrapper_prefix_is_populated(store: str, game_id: str) -> bool:
    """True when ``store``'s ``game_id`` has a prefix with its client installed.

    Prefers the recorded location, falling back to the internal default for a
    game installed before that was recorded. Requires the client executable to
    be present, so we only ever open a client into a real prefix — a genuinely
    unknown game still raises ``GameNotFoundError`` upstream, and an empty
    directory left by an abandoned install does not count.

    ``drive_c`` is located with :func:`resolve_drive_c` rather than by
    appending it to the prefix root. A Proton prefix keeps its C: drive at
    ``<root>/pfx/drive_c`` (only very old ones use ``<root>/drive_c``), so the
    hand-built path missed every modern prefix: this returned False for a
    fully-populated one, the caller raised ``GameNotFoundError``, and the
    launcher exited before opening the client. The install itself was already
    waiting on that window, so the UI sat on "INSTALLING UBISOFT CONNECT /
    Follow the Ubisoft Connect window" forever — reported from the field
    against Rayman Origins, whose prefix was a custom
    ``~/Games/prefixes/ubisoft/80`` recorded in ``ubisoft_id_map.json``.
    """
    spec = _SPECS.get(store)
    if spec is None:
        return False
    from unifideck.launcher.proton.infrastructure.prefix_layout import (
        resolve_drive_c,
    )

    candidates: list[Path] = []
    recorded = _recorded_prefix(spec, game_id)
    if recorded:
        candidates.append(Path(recorded))
    candidates.append(_data_dir() / "prefixes" / store / game_id)
    for candidate in candidates:
        drive_c = resolve_drive_c(candidate)
        if drive_c is not None and (drive_c / spec.client_rel).is_file():
            logger.info(
                "[%s] %s has a bootstrapped prefix at %s",
                store, game_id, candidate,
            )
            return True
    return False
