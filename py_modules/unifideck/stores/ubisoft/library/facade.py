"""facade.py — Public ``UbisoftLibrary`` surface.

# OP-57a | py_modules/unifideck/stores/ubisoft/library/facade.py | Depends: OP-55a
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ....core.types import Game
from ..config import UbisoftConfig
from ..id_map import UbisoftIdMap
from ..paths import UbisoftPrefixPaths
from .detection import _InstallDetector
from .fetch import _LibraryFetcher
from .manifest import _VisibleManifestProcessor

logger = logging.getLogger(__name__)


class UbisoftLibrary:
    """Ubisoft library."""

    def __init__(
        self,
        config: UbisoftConfig,
        paths: UbisoftPrefixPaths,
        id_map: UbisoftIdMap,
        queue_template_creation: Callable[[], None],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths
        self._id_map = id_map
        self._queue_template_creation = queue_template_creation
        self._detector = _InstallDetector(config, id_map)
        self._fetcher = _LibraryFetcher(config, paths, id_map)
        self._manifest = _VisibleManifestProcessor(
            config, id_map,
            load_json_file_safe=_InstallDetector.load_json_file_safe,
        )

    async def get_library(self) -> list[Game]:
        """Get library."""
        installed = await self.get_installed()
        games = await self._fetcher.fetch_local_binaries(installed)
        if games is None:
            self._queue_template_creation()
            games = []
        manifest = self._manifest.load_manifest()
        if manifest:
            games = self._manifest.apply_filter(
                games, installed, manifest, source_label='visible_manifest',
            )
        return games

    async def get_installed(self) -> dict[str, Any]:
        """Get installed."""
        return await self._detector.get_installed()

    def get_installed_game_info(self, game_id: str) -> dict[str, Any] | None:
        """Get installed game info."""
        return self._detector.get_installed_game_info(game_id)

    def find_game_executable(self, install_path: str) -> str | None:
        """Find game executable."""
        return self._detector.find_game_executable(install_path)

    async def write_install_marker(
        self, space_id: str, install_path: str, executable: str,
        game_title: str = '',
    ) -> None:
        """Write install marker."""
        await self._detector.write_install_marker(
            space_id, install_path, executable, game_title,
        )

    @staticmethod
    def get_game_official_url(game_id: str) -> str:
        """Get game official URL."""
        return _InstallDetector.get_game_official_url(game_id)
