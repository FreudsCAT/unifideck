"""
Load installed-state from Unifideck install markers.

OP-57c | py_modules/unifideck/stores/ubisoft/library/data_loader.py

``_DataLoader`` walks every per-game install directory under
``UbisoftConfig.default_install_base_expanded`` and reads each
``.unifideck-id`` marker into a dict keyed by ``install_id``.

These markers are written by the installer when a game install
completes and serve as the authoritative source of "what Unifideck has
installed". A game without a marker is considered uninstalled even if
its files exist on disk (typically a leftover from a failed install).
"""

from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from ..config import UbisoftConfig
    from ..parser import GameConfig
    from ..paths import UbisoftPrefixPaths

    ParseConfigurationsFn = Callable[[str], list[GameConfig]]
    ParseOwnershipFn = Callable[[str], list[int]]
logger = logging.getLogger(__name__)


class _DataLoader:
    """Data loader."""

    def __init__(
        self,
        *,
        config: UbisoftConfig,
        paths: UbisoftPrefixPaths,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths

    async def load_configurations(
        self,
        parse_configurations: ParseConfigurationsFn,
    ) -> list[GameConfig] | None:
        """Load configurations."""
        cfg_path = self._find_library_configurations_path()
        if not cfg_path:
            logger.info(
                "[UbisoftLibrary] no configurations binary found",
            )
            return None
        configs = await asyncio.to_thread(
            parse_configurations,
            cfg_path,
        )
        if not configs:
            logger.warning(
                "[UbisoftLibrary] configurations binary parsed but empty",
            )
            return None
        return configs

    async def load_ownership_set(
        self,
        parse_ownership: ParseOwnershipFn,
    ) -> set[int] | None:
        """Load ownership set."""
        ownership_path, user_id = self._discover_ownership_file()
        if not ownership_path:
            return None
        owned_ids = await asyncio.to_thread(
            parse_ownership,
            ownership_path,
        )
        owned_set = set(owned_ids)
        user_display = user_id[:8] if user_id else "?"
        logger.info(
            "[UbisoftLibrary] ownership: %d unique IDs (userId=%s…)",
            len(owned_set),
            user_display,
        )
        return owned_set

    def _find_library_configurations_path(self) -> str | None:
        """Find library configurations path."""
        for prefix_dir in (
            self._config.auth_prefix_dir_expanded,
            self._config.template_dir_expanded,
        ):
            cfg_path = self._paths.find_configurations(prefix_dir)
            if cfg_path:
                return cfg_path
        return None

    def _discover_ownership_file(
        self,
    ) -> tuple[str | None, str]:
        """Discover ownership file."""
        for prefix_dir in (
            self._config.auth_prefix_dir_expanded,
            self._config.template_dir_expanded,
        ):
            prefix_p = Path(prefix_dir)
            if not prefix_p.is_dir():
                continue
            for layout_sub in ("", "pfx"):
                base = prefix_p
                if layout_sub:
                    base = base / layout_sub
                ownership_dir = base / self._config.ownership_relative_path
                if not ownership_dir.is_dir():
                    continue
                try:
                    entries = [e for e in ownership_dir.iterdir() if e.is_file()]
                except OSError:
                    continue
                if entries:
                    entry = entries[0]
                    user_id = entry.name
                    return str(entry), user_id
        return (None, "")
