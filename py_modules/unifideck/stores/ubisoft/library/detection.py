"""detection.py — Public ``_InstallDetector`` surface for the library.

# OP-57f | py_modules/unifideck/stores/ubisoft/library/detection.py | Depends: (none)
"""
from __future__ import annotations

import logging
from typing import Any

from ..config import UbisoftConfig
from ..id_map import UbisoftIdMap
from .detection_cascade import _DetectionCascade
from .detection_helpers import (
    find_game_executable as _find_game_executable_impl,
    in_prefix_game_roots,
    load_json_file_safe as _load_json_file_safe_impl,
    write_install_marker as _write_install_marker_impl,
)

logger = logging.getLogger(__name__)


class _InstallDetector:
    """Install detector."""

    def __init__(
        self, config: UbisoftConfig, id_map: UbisoftIdMap,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._id_map = id_map
        self._cascade = _DetectionCascade(self)

    @staticmethod
    def find_game_executable(install_path: str) -> str | None:
        """Find game executable."""
        return _find_game_executable_impl(install_path)

    async def write_install_marker(
        self, space_id: str, install_path: str, executable: str,
        game_title: str = '',
    ) -> None:
        """Write install marker."""
        await _write_install_marker_impl(
            space_id=space_id, install_path=install_path,
            executable=executable, game_title=game_title,
        )

    @staticmethod
    def load_json_file_safe(path: str) -> Any | None:
        """Load JSON file safe."""
        return _load_json_file_safe_impl(path)

    @staticmethod
    def get_game_official_url(game_id: str) -> str:
        """Get game official URL."""
        return f'https://store.ubi.com/us/game?pid={game_id}'

    async def get_installed(self) -> dict[str, Any]:
        """Get installed."""
        installed: dict[str, Any] = {}
        for space_id in list(self._id_map._cache.keys()):
            info = self.get_installed_game_info(space_id)
            if info:
                installed[space_id] = info
        return installed

    def get_installed_game_info(self, game_id: str) -> dict[str, Any] | None:
        """Get installed game info."""
        space_id = game_id
        for prefix_path in self._config.iter_game_prefix_paths():
            info = self._detect_installed_game(space_id, prefix_path)
            if info:
                self._auto_resolve_id_from_registry(space_id, prefix_path, info)
                return info
        return None

    def _auto_resolve_id_from_registry(
        self, space_id: str, prefix_path: str, game_info: dict[str, Any],
    ) -> None:
        """Auto resolve ID from registry."""
        if game_info.get('install_id'):
            return
        try:
            game_id = self._id_map.extract_game_id_from_registry(prefix_path)
        except Exception:
            game_id = None
        if game_id:
            game_info['install_id'] = game_id
            self._id_map.merge_entry(space_id, {'install_id': game_id})

    async def _auto_resolve_missing_id(
        self, space_id: str, prefix_path: str, game_info: dict[str, Any],
    ) -> None:
        """Auto resolve missing ID."""
        if game_info.get('install_id'):
            return
        try:
            await self._id_map.refresh_from_configurations(space_id)
        except Exception as e:
            logger.debug('[Ubisoft.detection] refresh failed: %s', e)
        entry = self._id_map.get_entry(space_id)
        if entry.get('install_id'):
            game_info['install_id'] = entry['install_id']

    def _detect_installed_game(
        self, space_id: str, prefix_path: str,
    ) -> dict[str, Any] | None:
        """Detect installed game."""
        known_name = self._get_game_name(space_id) or ''
        normalised = self._id_map.normalize_for_matching(known_name)
        return self._cascade.detect_via_marker(
            space_id, known_name,
            in_prefix_game_roots(prefix_path),
        ) or self._cascade.detect_via_prefix_install_state(
            space_id,
            in_prefix_game_roots(prefix_path),
            normalised, known_name,
            self._cascade._default_check_install_state,
        ) or self._cascade.detect_via_external_roots(
            space_id, self._cascade._helpers.get_external_game_roots(),
            normalised, known_name,
            self._cascade._default_check_install_state,
        ) or self._cascade.detect_via_registry_install_id(
            space_id, prefix_path, known_name,
            self._cascade._default_check_install_state,
        )

    def _get_game_name(self, space_id: str) -> str | None:
        """Get game name."""
        entry = self._id_map.get_entry(space_id)
        name = entry.get('name')
        return name if isinstance(name, str) else None
