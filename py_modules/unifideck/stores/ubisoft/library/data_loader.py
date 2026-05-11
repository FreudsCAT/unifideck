"""data_loader.py — Resolve and load the configurations / ownership files.

# OP-57c | py_modules/unifideck/stores/ubisoft/library/data_loader.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import os
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
        self, *, config: UbisoftConfig, paths: UbisoftPrefixPaths,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths

    async def load_configurations(
        self, parse_configurations: ParseConfigurationsFn,
    ) -> list[GameConfig] | None:
        """Load configurations."""
        cfg_path = self._find_library_configurations_path()
        if cfg_path is None:
            return None
        try:
            return await asyncio.to_thread(parse_configurations, cfg_path)
        except Exception as e:
            logger.warning(
                '[Ubisoft.library] configurations parse failed: %s', e,
            )
            return None

    async def load_ownership_set(
        self, parse_ownership: ParseOwnershipFn,
    ) -> set[int] | None:
        """Load ownership set."""
        ownership_path, _label = self._discover_ownership_file()
        if not ownership_path:
            return None
        try:
            owned = await asyncio.to_thread(parse_ownership, ownership_path)
        except Exception as e:
            logger.warning('[Ubisoft.library] ownership parse failed: %s', e)
            return None
        return set(owned) if owned else set()

    def _find_library_configurations_path(self) -> str | None:
        """Find library configurations path."""
        for prefix in self._config.iter_game_prefix_paths():
            cfg = self._paths.find_configurations(prefix)
            if cfg:
                return cfg
        auth = self._config.auth_prefix_dir_expanded
        if auth and os.path.isdir(auth):
            cfg = self._paths.find_configurations(auth)
            if cfg:
                return cfg
        return None

    def _discover_ownership_file(self) -> tuple[str | None, str]:
        """Discover ownership file."""
        relative = self._config.ownership_relative_path
        for prefix in self._config.iter_game_prefix_paths():
            cache_dir = Path(prefix) / relative
            cache_dir_pfx = Path(prefix) / 'pfx' / relative
            for candidate_dir in (cache_dir, cache_dir_pfx):
                if not candidate_dir.is_dir():
                    continue
                files = sorted(
                    candidate_dir.iterdir(), key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for entry in files:
                    if entry.is_file() and entry.stat().st_size > 0:
                        return str(entry), str(prefix)
        return None, ''
