from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .registry_io import _apply_windows_locale
from .resolver import get_unifideck_language

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
logger = logging.getLogger(__name__)
def apply_amazon_language(
    prefix_path: str, config: ConfigManager | None = None,
) -> bool:
    """Apply AMAZON language."""
    language = get_unifideck_language(config)
    logger.info(
        "[language_setup.amazon] applying %s to prefix=%s",
        language, prefix_path,
    )
    return _apply_windows_locale(prefix_path, language)
