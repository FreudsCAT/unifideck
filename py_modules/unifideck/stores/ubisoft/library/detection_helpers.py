"""detection_helpers.py — Filesystem helpers used by detection logic.

# OP-57h | py_modules/unifideck/stores/ubisoft/library/detection_helpers.py | Depends: (none)
"""
from __future__ import annotations

import datetime
import glob
import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .detection import _InstallDetector

logger = logging.getLogger(__name__)
_EXE_SKIP_PATTERNS = (
    'unins', 'setup', 'install', 'crash', 'redist', 'vcredist',
    'dxsetup', 'dotnet', 'upc', 'uplay',
)
_GAME_INSTALL_MIN_SIZE = 100 * 1024 * 1024
_IN_PREFIX_GAMES_PATH = str(
    Path('drive_c') / 'Program Files (x86)' / 'Ubisoft'
    / 'Ubisoft Game Launcher' / 'games'
)
_INSTALL_MARKER_FILENAME = '.unifideck_ubisoft'


def load_json_file_safe(path: str) -> Any | None:
    """Load JSON file safe."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.debug('[Ubisoft.detection] json load %s: %s', path, e)
        return None


def walk_install_candidates(
    roots: list[str],
) -> Iterator[tuple[str, str]]:
    """Walk install candidates."""
    seen: set[str] = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            full = os.path.join(root, entry)
            if not os.path.isdir(full):
                continue
            real = os.path.realpath(full)
            if real in seen:
                continue
            seen.add(real)
            yield entry, full


def in_prefix_game_roots(prefix_path: str) -> list[str]:
    """In prefix game roots."""
    return [
        os.path.join(prefix_path, _IN_PREFIX_GAMES_PATH),
        os.path.join(prefix_path, 'pfx', _IN_PREFIX_GAMES_PATH),
    ]


def find_game_executable(install_path: str) -> str | None:
    """Find game executable."""
    if not install_path or not os.path.isdir(install_path):
        return None
    candidates: list[tuple[int, str]] = []
    try:
        for root, _dirs, files in os.walk(install_path):
            depth = root[len(install_path):].count(os.sep)
            if depth >= 3:
                continue
            for f in files:
                lower = f.lower()
                if not lower.endswith('.exe'):
                    continue
                if any(p in lower for p in _EXE_SKIP_PATTERNS):
                    continue
                full = os.path.join(root, f)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if size < 1024:
                    continue
                candidates.append((size, full))
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def looks_like_game_install(path: str) -> bool:
    """Looks like game install."""
    if not path or not os.path.isdir(path):
        return False
    try:
        for root, _dirs, files in os.walk(path):
            depth = root[len(path):].count(os.sep)
            if depth >= 2:
                break
            for f in files:
                if f.lower().endswith('.exe'):
                    return True
    except OSError:
        return False
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
                if total > _GAME_INSTALL_MIN_SIZE:
                    return True
    except OSError:
        return False
    return False


async def write_install_marker(
    space_id: str,
    install_path: str,
    executable: str,
    game_title: str = '',
) -> None:
    """Write install marker."""
    write_marker_sync(install_path, space_id, game_title or '', executable=executable)


def write_marker_sync(
    install_path: str,
    space_id: str,
    title: str,
    executable: str = '',
) -> None:
    """Write marker sync."""
    if not install_path or not os.path.isdir(install_path):
        return
    marker = os.path.join(install_path, _INSTALL_MARKER_FILENAME)
    payload = {
        'space_id': space_id,
        'title': title,
        'executable': executable,
        'created_at': datetime.datetime.utcnow().isoformat() + 'Z',
    }
    try:
        with open(marker, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
    except OSError as e:
        logger.debug('[Ubisoft.detection] marker write: %s', e)


class _DetectionHelpers:
    """Detection helpers."""

    def __init__(self, parent: _InstallDetector) -> None:
        """Initialize the instance."""
        self._parent = parent

    def get_external_game_roots(self) -> list[str]:
        """Get external game roots."""
        config = self._parent._config
        roots: list[str] = []
        self._append_custom_path_root(roots, config)
        self._append_mounted_media_roots(roots)
        return self._dedup_roots_by_realpath(roots)

    @staticmethod
    def _append_custom_path_root(roots: list[str], config: Any) -> None:
        """Append custom path root."""
        for path in (
            config.default_install_base_expanded,
            config.sdcard_install_base,
        ):
            if path and os.path.isdir(path):
                roots.append(path)

    @staticmethod
    def _append_mounted_media_roots(roots: list[str]) -> None:
        """Append mounted media roots."""
        for media_parent in ('/run/media', '/media'):
            if not os.path.isdir(media_parent):
                continue
            try:
                for entry in sorted(os.listdir(media_parent)):
                    parent = Path(media_parent) / entry
                    if parent.is_dir():
                        _DetectionHelpers._append_sub_mount_roots(parent, roots)
            except OSError:
                continue

    @staticmethod
    def _append_sub_mount_roots(parent: Path, roots: list[str]) -> None:
        """Append sub mount roots."""
        for pattern in ('Games/Ubisoft', '*/Games/Ubisoft'):
            for hit in glob.glob(str(parent / pattern)):
                if os.path.isdir(hit):
                    roots.append(hit)

    @staticmethod
    def _dedup_roots_by_realpath(roots: list[str]) -> list[str]:
        """Dedup roots by realpath."""
        seen: set[str] = set()
        out: list[str] = []
        for r in roots:
            real = os.path.realpath(r)
            if real not in seen:
                seen.add(real)
                out.append(r)
        return out
