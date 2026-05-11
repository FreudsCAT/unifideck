"""exe_resolver.py — Find the playable executable inside a GOG install.

# OP-50e | py_modules/unifideck/stores/gog/exe_resolver.py | Depends: OP-04c

GOG installs end up with a mix of installer leftovers (vcredist, dxsetup),
wrapper launchers (dosbox.exe, scummvm.exe), and the actual game .exe.
The resolver prefers the explicit ``primaryTask`` from a ``goggame-*.info``
manifest, then falls back to a ``start.sh`` script, then to the largest
non-skip .exe in the tree.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_SKIP_EXE_PATTERNS = (
    'unins', 'setup', 'install', 'crash', 'redist', 'vcredist',
    'vc_redist', 'dxsetup', 'physx', 'dotnet', 'directx',
)
_ROOT_DATA_EXTENSIONS = ('.arch05', '.forge')
_WRAPPER_EXE_NAMES = {'dosbox.exe', 'scummvm.exe'}


class GOGExeResolver:
    """GOG exe resolver."""

    def find(self, install_path: str) -> str | None:
        """Find."""
        result = self._resolve(install_path)
        return result[0] if result else None

    def find_with_workdir(self, install_path: str) -> tuple[str, str] | None:
        """Find with workdir."""
        return self._resolve(install_path)

    def _resolve(self, install_path: str) -> tuple[str, str] | None:
        """Resolve."""
        if not install_path or not os.path.isdir(install_path):
            return None
        search_dirs = self._build_search_dirs(install_path)
        result = self._resolve_via_goggame_info(install_path, search_dirs)
        if result:
            return result
        result = self._resolve_via_start_sh(search_dirs)
        if result:
            return result
        return self._resolve_via_largest_exe(search_dirs)

    @staticmethod
    def _build_search_dirs(install_path: str) -> list[str]:
        """Build search dirs."""
        dirs = [install_path]
        for entry in ('game', 'bin'):
            sub = os.path.join(install_path, entry)
            if os.path.isdir(sub):
                dirs.append(sub)
        try:
            for entry in sorted(os.listdir(install_path)):
                full = os.path.join(install_path, entry)
                if os.path.isdir(full) and full not in dirs:
                    dirs.append(full)
        except OSError:
            pass
        return dirs

    def _resolve_via_goggame_info(
        self, install_path: str, search_dirs: list[str],
    ) -> tuple[str, str] | None:
        """Resolve via goggame info."""
        primary, root_dir = self._load_primary_play_task(search_dirs)
        if not primary:
            return None
        return self._resolve_play_task_paths(install_path, root_dir, primary)

    def _load_primary_play_task(
        self, search_dirs: list[str],
    ) -> tuple[dict[str, Any] | None, str]:
        """Load primary play task."""
        info, root_dir = self._find_goggame_info(search_dirs)
        if info is None:
            return None, ''
        play_tasks = info.get('playTasks') or []
        if not isinstance(play_tasks, list):
            return None, root_dir
        for task in play_tasks:
            if not isinstance(task, dict):
                continue
            if task.get('isPrimary') or task.get('isPrimaryTask'):
                return task, root_dir
        return (play_tasks[0] if play_tasks else None), root_dir

    def _resolve_play_task_paths(
        self, install_path: str, root_dir: str, primary: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Resolve play task paths."""
        rel = primary.get('path') or ''
        workdir_rel = primary.get('workingDir') or ''
        if not isinstance(rel, str):
            return None
        base = root_dir or install_path
        exe_path = os.path.normpath(os.path.join(base, rel))
        if not os.path.isfile(exe_path):
            return None
        wrapper = self._check_wrapper_override(install_path, base, primary)
        if wrapper:
            return wrapper
        if isinstance(workdir_rel, str) and workdir_rel:
            workdir = os.path.normpath(os.path.join(base, workdir_rel))
        else:
            workdir = os.path.dirname(exe_path)
        return exe_path, workdir

    @staticmethod
    def _find_goggame_info(
        search_dirs: list[str],
    ) -> tuple[dict[str, Any] | None, str]:
        """Find goggame info."""
        for dirpath in search_dirs:
            for match in glob.glob(os.path.join(dirpath, 'goggame-*.info')):
                try:
                    with open(match, encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        return data, dirpath
                except (OSError, json.JSONDecodeError):
                    continue
        return None, ''

    def _check_wrapper_override(
        self, install_path: str, root_dir: str, primary_task: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Check wrapper override."""
        if not self._has_root_data_files(install_path):
            return None
        rel = (primary_task.get('path') or '').lower()
        for wrapper in _WRAPPER_EXE_NAMES:
            if wrapper in rel:
                full = os.path.normpath(os.path.join(root_dir, rel))
                if os.path.isfile(full):
                    return full, os.path.dirname(full)
        return None

    @staticmethod
    def _has_root_data_files(install_path: str) -> bool:
        """Has root data files."""
        try:
            for name in os.listdir(install_path):
                if name.lower().endswith(_ROOT_DATA_EXTENSIONS):
                    return True
        except OSError:
            pass
        return False

    @staticmethod
    def _resolve_via_start_sh(
        search_dirs: list[str],
    ) -> tuple[str, str] | None:
        """Resolve via start sh."""
        for dirpath in search_dirs:
            path = os.path.join(dirpath, 'start.sh')
            if os.path.isfile(path):
                return path, dirpath
        return None

    @staticmethod
    def _resolve_via_largest_exe(
        search_dirs: list[str],
    ) -> tuple[str, str] | None:
        """Resolve via largest exe."""
        best: tuple[int, str] | None = None
        for dirpath in search_dirs:
            try:
                for root, _dirs, files in os.walk(dirpath):
                    depth = root[len(dirpath):].count(os.sep)
                    if depth >= 3:
                        continue
                    for name in files:
                        lower = name.lower()
                        if not lower.endswith('.exe'):
                            continue
                        if any(p in lower for p in _SKIP_EXE_PATTERNS):
                            continue
                        full = os.path.join(root, name)
                        try:
                            size = os.path.getsize(full)
                        except OSError:
                            continue
                        if best is None or size > best[0]:
                            best = (size, full)
            except OSError:
                continue
        if best is None:
            return None
        path = best[1]
        return path, os.path.dirname(path)


def parse_size_string(size_str: str) -> int:
    """Parse size string."""
    if not size_str:
        return 0
    s = size_str.strip().upper()
    multipliers = {'KB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3, 'TB': 1024 ** 4}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            try:
                return int(float(s[:-len(suffix)].strip()) * mult)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def get_game_id_from_goggame_filename(filename: str) -> str | None:
    """Get game ID from goggame filename."""
    name = Path(filename).name
    if not name.startswith('goggame-'):
        return None
    if name.endswith('.info'):
        return name[len('goggame-'):-len('.info')]
    if name.endswith('.script'):
        return name[len('goggame-'):-len('.script')]
    return None
