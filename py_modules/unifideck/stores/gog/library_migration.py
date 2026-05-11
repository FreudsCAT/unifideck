"""library_migration.py — Migrate legacy install markers in-place.

# OP-50d | py_modules/unifideck/stores/gog/library_migration.py | Depends: OP-50c

Older Unifideck builds wrote a flat ``.unifideck-id`` marker containing
only the GOG id. The current scheme is a JSON document with metadata
(title, install_id, language). This module rewrites old markers to the
new format on the next library scan.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .library import GOGLibrary

logger = logging.getLogger(__name__)
_INSTALL_MARKER = '.unifideck-id'


class _MarkerMigration:
    """Marker migration."""

    def __init__(self, parent: GOGLibrary) -> None:
        """Initialize the instance."""
        self._parent = parent

    def migrate_old_markers(self) -> dict[str, int]:
        """Migrate old markers."""
        download_dir = os.path.expanduser(self._parent._config.download_dir)
        stats = {'scanned': 0, 'migrated': 0, 'skipped': 0}
        if not os.path.isdir(download_dir):
            return stats
        for entry in sorted(os.listdir(download_dir)):
            game_dir = os.path.join(download_dir, entry)
            if not os.path.isdir(game_dir):
                continue
            marker_path = os.path.join(game_dir, _INSTALL_MARKER)
            if not os.path.isfile(marker_path):
                continue
            stats['scanned'] += 1
            if self._migrate_one_marker(game_dir, marker_path):
                stats['migrated'] += 1
            else:
                stats['skipped'] += 1
        return stats

    def _migrate_one_marker(self, game_dir: str, marker_path: str) -> str:
        """Migrate one marker."""
        content = self._read_marker_content(marker_path)
        if content is None:
            return ''
        if self._marker_is_new_format(content):
            return ''
        legacy_id = self._extract_legacy_id(content)
        if not legacy_id:
            return ''
        new_data = self._build_new_marker_payload(game_dir, legacy_id)
        return self._write_new_marker(marker_path, new_data, game_dir)

    @staticmethod
    def _read_marker_content(marker_path: str) -> str | None:
        """Read marker content."""
        try:
            with open(marker_path, encoding='utf-8') as f:
                return f.read()
        except OSError as e:
            logger.debug('[GOGMigration] read %s: %s', marker_path, e)
            return None

    @staticmethod
    def _marker_is_new_format(content: str) -> bool:
        """Marker is new format."""
        stripped = content.strip()
        if not stripped.startswith('{'):
            return False
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            return False
        return True

    @staticmethod
    def _extract_legacy_id(content: str) -> str | None:
        """Extract legacy ID."""
        candidate = content.strip().split('\n', 1)[0].strip()
        return candidate if candidate.isdigit() else None

    def _build_new_marker_payload(
        self, game_dir: str, old_id: str,
    ) -> dict[str, Any]:
        """Build new marker payload."""
        info = self._find_first_goggame_info(game_dir)
        payload: dict[str, Any] = {
            'game_id': old_id,
            'install_path': game_dir,
            'title': '',
            'language': '',
        }
        if info:
            try:
                with open(info, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    payload['title'] = (
                        data.get('name') or data.get('rootGameId') or ''
                    )
                    payload['language'] = data.get('language') or ''
            except (OSError, json.JSONDecodeError):
                pass
        return payload

    @staticmethod
    def _write_new_marker(
        marker_path: str,
        new_data: dict[str, Any],
        game_dir: str,
    ) -> str:
        """Write new marker."""
        try:
            with open(marker_path, 'w', encoding='utf-8') as f:
                json.dump(new_data, f, indent=2)
        except OSError as e:
            logger.warning(
                '[GOGMigration] write %s: %s', marker_path, e,
            )
            return ''
        logger.info('[GOGMigration] migrated marker in %s', game_dir)
        return new_data.get('game_id', '')

    @staticmethod
    def _find_first_goggame_info(directory: str) -> str | None:
        """Find first goggame info."""
        for match in glob.glob(os.path.join(directory, 'goggame-*.info')):
            return match
        for sub in ('game', 'bin'):
            subpath = os.path.join(directory, sub)
            for match in glob.glob(
                os.path.join(subpath, 'goggame-*.info'),
            ):
                return match
        return None
