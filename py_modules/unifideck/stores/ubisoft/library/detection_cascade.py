"""detection_cascade.py — Cascade of fallback strategies for install detection.

# OP-57g | py_modules/unifideck/stores/ubisoft/library/detection_cascade.py | Depends: (none)

Tries (in order): the install-marker file, in-prefix UPC games dir,
configured external game roots (default + sd-card + mounted media),
and finally the per-prefix Wine registry.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .detection_helpers import (
    _DetectionHelpers,
    load_json_file_safe,
    looks_like_game_install,
    walk_install_candidates,
    write_marker_sync,
)
from .wine_path import wine_path_to_linux

if TYPE_CHECKING:
    from .detection import _InstallDetector

logger = logging.getLogger(__name__)
_INSTALL_MARKER_FILENAME = '.unifideck_ubisoft'


class _DetectionCascade:
    """Detection cascade."""

    def __init__(self, parent: _InstallDetector) -> None:
        """Initialize the instance."""
        self._parent = parent
        self._helpers = _DetectionHelpers(parent)

    def detect_via_marker(
        self, space_id: str, known_name: str, search_roots: list[str],
    ) -> dict[str, Any] | None:
        """Detect via marker."""
        for _entry, game_dir in walk_install_candidates(search_roots):
            marker_data = self._load_marker_for_space(game_dir, space_id)
            if marker_data is None:
                continue
            return self._build_marker_result(
                space_id, known_name, game_dir,
                os.path.basename(game_dir), marker_data,
            )
        return None

    @staticmethod
    def _load_marker_for_space(
        game_dir: str, space_id: str,
    ) -> dict | None:
        """Load marker for space."""
        path = os.path.join(game_dir, _INSTALL_MARKER_FILENAME)
        marker = load_json_file_safe(path)
        if not isinstance(marker, dict):
            return None
        if marker.get('space_id') == space_id:
            return marker
        return None

    def _build_marker_result(
        self, space_id: str, known_name: str, game_dir: str,
        folder: str, marker_data: dict,
    ) -> dict[str, Any]:
        """Build marker result."""
        executable = self._resolve_marker_executable(marker_data, game_dir)
        return {
            'space_id': space_id,
            'install_path': game_dir,
            'install_dir': folder,
            'executable': executable,
            'name': marker_data.get('title') or known_name,
            'install_id': '',
        }

    def _resolve_marker_executable(
        self, marker_data: dict, install_path: str,
    ) -> str:
        """Resolve marker executable."""
        rel = marker_data.get('executable')
        if isinstance(rel, str) and rel:
            full = os.path.join(install_path, rel)
            if os.path.isfile(full):
                return full
        return self._parent.find_game_executable(install_path) or ''

    def detect_via_prefix_install_state(
        self,
        space_id: str,
        prefix_game_roots: list[str],
        normalized_known_name: str,
        known_name: str,
        check_install_state: Callable[[str], bool],
    ) -> dict[str, Any] | None:
        """Detect via prefix install state."""
        for _entry, game_dir in walk_install_candidates(prefix_game_roots):
            folder = os.path.basename(game_dir)
            if not self.fuzzy_folder_match(folder, normalized_known_name):
                continue
            state = os.path.join(game_dir, 'uplay_install.state')
            if os.path.isfile(state) and not check_install_state(state):
                continue
            if not looks_like_game_install(game_dir):
                continue
            self._write_unifideck_marker(game_dir, space_id, known_name)
            return self.build_install_info(space_id, game_dir, known_name)
        return None

    def detect_via_external_roots(
        self,
        space_id: str,
        external_game_roots: list[str],
        normalized_known_name: str,
        known_name: str,
        check_install_state: Callable[[str], bool],
    ) -> dict[str, Any] | None:
        """Detect via external roots."""
        for _entry, game_dir in walk_install_candidates(external_game_roots):
            folder = os.path.basename(game_dir)
            if not self.fuzzy_folder_match(folder, normalized_known_name):
                continue
            if not looks_like_game_install(game_dir):
                continue
            self._write_unifideck_marker(game_dir, space_id, known_name)
            return self.build_install_info(space_id, game_dir, known_name)
        return None

    def detect_via_registry_install_id(
        self,
        space_id: str,
        prefix_path: str,
        known_name: str,
        check_install_state: Callable[[str], bool],
    ) -> dict[str, Any] | None:
        """Detect via registry install ID."""
        for reg_name in ('system.reg', os.path.join('pfx', 'system.reg')):
            reg_path = os.path.join(prefix_path, reg_name)
            if not os.path.isfile(reg_path):
                continue
            install_id = self._parent._id_map.get_entry(space_id).get(
                'install_id',
            )
            if not install_id:
                continue
            pattern = self._build_registry_pattern(install_id)
            result = self._try_registry_file(
                reg_path, pattern, space_id, install_id, prefix_path,
                known_name, check_install_state,
            )
            if result is not None:
                return result
        return None

    @staticmethod
    def _build_registry_pattern(install_id: str) -> re.Pattern:
        """Build registry pattern."""
        return re.compile(
            (
                r'\[Software\\\\Wow6432Node\\\\Ubisoft\\\\Launcher\\\\'
                r'Installs\\\\' + re.escape(install_id) + r'\][^\[]*?'
                r'"InstallDir"\s*=\s*"([^"]*)"'
            ),
            re.DOTALL,
        )

    def _try_registry_file(
        self,
        reg_path: str,
        pattern: re.Pattern,
        space_id: str,
        install_id: str,
        prefix_path: str,
        known_name: str,
        check_install_state: Callable[[str], bool],
    ) -> dict[str, Any] | None:
        """Try registry file."""
        try:
            with open(reg_path, encoding='utf-8', errors='replace') as f:
                content = f.read()
        except OSError:
            return None
        match = pattern.search(content)
        if not match:
            return None
        wine_path = match.group(1).replace('\\\\', '\\')
        linux_path = wine_path_to_linux(wine_path, prefix_path)
        if not linux_path:
            return None
        if not self._validate_registry_install(linux_path, check_install_state):
            return None
        self._write_unifideck_marker(linux_path, space_id, known_name)
        return self.build_install_info(space_id, linux_path, known_name)

    @staticmethod
    def _validate_registry_install(
        linux_path: str | None, check_install_state: Callable[[str], bool],
    ) -> bool:
        """Validate registry install."""
        if not linux_path or not os.path.isdir(linux_path):
            return False
        state = os.path.join(linux_path, 'uplay_install.state')
        if os.path.isfile(state) and not check_install_state(state):
            return False
        return looks_like_game_install(linux_path)

    def build_install_info(
        self, space_id: str, game_dir: str, title_hint: str,
    ) -> dict[str, Any]:
        """Build install info."""
        executable = self._parent.find_game_executable(game_dir) or ''
        return {
            'space_id': space_id,
            'install_path': game_dir,
            'install_dir': os.path.basename(game_dir),
            'executable': executable,
            'name': title_hint,
            'install_id': self._parent._id_map.get_entry(space_id).get(
                'install_id', '',
            ),
        }

    def fuzzy_folder_match(
        self, folder_name: str, normalized_known_name: str,
    ) -> bool:
        """Fuzzy folder match."""
        if not normalized_known_name:
            return False
        norm = self._parent._id_map.normalize_for_matching(folder_name)
        return (
            norm == normalized_known_name
            or normalized_known_name in norm
            or norm in normalized_known_name
        )

    def _write_unifideck_marker(
        self, install_path: str, space_id: str, title: str,
    ) -> None:
        """Write unifideck marker."""
        write_marker_sync(install_path, space_id, title)

    @staticmethod
    def _default_check_install_state(state_file: str) -> bool:
        """Default check install state."""
        from ..parser import check_install_state as _impl
        return _impl(state_file)
