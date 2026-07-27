"""Detect whether a Ubisoft title already has a bootstrapped prefix.

Extracted from ``dispatcher.py`` to keep that file under the 550 LOC
volumetry cap. Pure filesystem probe — no dispatch logic.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _ubisoft_has_populated_prefix(game_id: str) -> bool:
    """True if ``game_id`` is a known Ubisoft title with a populated prefix.

    Used when the install env token was lost (Steam dropped the launch
    option) so ``_detect_special_action`` saw a plain launch, yet the title
    has no games.map row. Mirrors ``_ubisoft_prefix_path`` resolution: prefer
    the recorded ``prefix_path`` in ``ubisoft_id_map.json``, else the fixed
    internal default — and require upc.exe present so we only open UPC into a
    real prefix (genuinely-missing games still raise GameNotFoundError).

    ``drive_c`` is located with :func:`resolve_drive_c` rather than by
    appending it to the prefix root. A Proton prefix keeps its C: drive at
    ``<root>/pfx/drive_c`` (only very old ones use ``<root>/drive_c``), so
    the hand-built path missed every modern prefix: this returned False for a
    fully-populated one, the caller raised ``GameNotFoundError``, and the
    launcher exited before opening UPC. The install itself was already
    waiting on that UPC window, so the UI sat on "INSTALLING UBISOFT
    CONNECT / Follow the Ubisoft Connect window" forever — reported from the
    field against Rayman Origins, whose prefix was a custom
    ``~/Games/prefixes/ubisoft/80`` recorded in ``ubisoft_id_map.json``.
    """
    from unifideck.launcher.proton.infrastructure.prefix_layout import (
        resolve_drive_c,
    )

    id_map_file = Path(
        "~/.local/share/unifideck/ubisoft_id_map.json",
    ).expanduser()
    try:
        data = json.loads(id_map_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or game_id not in data:
        return False
    entry = data.get(game_id)
    recorded = entry.get("prefix_path") if isinstance(entry, dict) else None
    upc_rel = (
        Path("Program Files (x86)")
        / "Ubisoft"
        / "Ubisoft Game Launcher"
        / "upc.exe"
    )
    candidates: list[Path] = []
    if isinstance(recorded, str) and recorded:
        candidates.append(Path(recorded))
    candidates.append(
        Path("~/.local/share/unifideck/prefixes/ubisoft").expanduser()
        / game_id,
    )
    for candidate in candidates:
        drive_c = resolve_drive_c(candidate)
        if drive_c is not None and (drive_c / upc_rel).is_file():
            return True
    return False
