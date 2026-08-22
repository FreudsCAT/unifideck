from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .registry_io import _apply_windows_locale
from .resolver import get_unifideck_language

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
logger = logging.getLogger(__name__)
def apply_battlenet_language(
    prefix_path: str, config: ConfigManager | None = None,
) -> bool:
    """Apply BATTLE.NET language.

    The client's own UI language is a separate mechanism —
    ``launcher/wrapper_locale`` seeds it into ``Battle.net.config`` — and this
    is the layer under it: the Windows locale a Blizzard *game* reads when it
    picks its own default. Measured on this Deck 2026-08-23, the Battle.net
    prefixes reported ``LocaleName="en-US"`` / ``sCountry="United States"``
    however the plugin was configured, because Battle.net was the one wrapper
    store never wired into this package.

    Same shape as :func:`~.amazon.apply_amazon_language`, and deliberately so.
    """
    language = get_unifideck_language(config)
    logger.info(
        "[language_setup.battlenet] applying %s to prefix=%s",
        language, prefix_path,
    )
    return _apply_windows_locale(prefix_path, language)
