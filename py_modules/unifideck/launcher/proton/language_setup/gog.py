from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .matchers import smart_match_gog_language
from .registry_io import _atomic_write_text
from .resolver import GOG_DISPLAY_NAMES, get_unifideck_language

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
logger = logging.getLogger(__name__)
def _find_goggame_info(game_id: str, install_dir: str) -> str | None:
    """Find goggame info."""
    search_dir = install_dir
    for _ in range(4):
        candidate = str(Path(search_dir) / f"goggame-{game_id}.info")
        if Path(candidate).exists():
            return candidate
        candidates = [
            str(p) for p in Path(search_dir).glob("goggame-*.info")
        ]
        matching = [
            c for c in candidates
            if Path(c).name.startswith(f"goggame-{game_id}")
        ]
        if matching:
            return matching[0]
        parent = str(Path(search_dir).parent)
        if parent == search_dir:
            break
        search_dir = parent
    return None

def apply_gog_language(
    game_id: str, install_dir: str, config: ConfigManager | None = None,
) -> bool:

    """Apply GOG language."""
    language = get_unifideck_language(config)
    logger.info(
        "[language_setup.gog] applying %s to game=%s dir=%s",
        language, game_id, install_dir,
    )
    info_path = _find_goggame_info(game_id, install_dir)
    if info_path is None:
        logger.info(
            "[language_setup.gog] no goggame-%s.info found (searched "
            "4 levels up from %s), skipping", game_id, install_dir,
        )
        return False
    try:
        with Path(info_path).open(encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, json.JSONDecodeError) as err:
        logger.warning(
            "[language_setup.gog] read %s failed: %s", info_path, err,
        )
        return False
    installed = info.get("languages", [])
    matched = smart_match_gog_language(language, installed)
    if matched is None:
        matched = language
    display_name = GOG_DISPLAY_NAMES.get(matched, matched)
    info["language"] = display_name
    info["languages"] = [matched]
    try:
        _atomic_write_text(info_path, json.dumps(info, indent=2))
    except OSError as err:
        logger.warning(
            "[language_setup.gog] write %s failed: %s", info_path, err,
        )
        return False
    logger.info(
        "[language_setup.gog] set %s → %s (%s)",
        info_path, matched, display_name,
    )
    return True
