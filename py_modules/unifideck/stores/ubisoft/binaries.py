"""binaries.py — Resolve umu-run / Proton / Python plus build env dicts.

# OP-55d | py_modules/unifideck/stores/ubisoft/binaries.py | Depends: OP-07a

UPC needs to run inside a Proton-managed Wine prefix wrapped by umu-run.
Decky's plugin process is launched by systemd without the user's
graphical session env, so DISPLAY / WAYLAND_DISPLAY / DBUS / XAUTHORITY
also get scraped from the live Steam / gamescope-session process.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from .config import UbisoftConfig

logger = logging.getLogger(__name__)
_DISPLAY_ENV_VARS = (
    'DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'XAUTHORITY',
)
_PROTON_OFFICIAL_NAMES = (
    'Proton - Experimental', 'Proton 10.0', 'Proton 9.0 (Beta)',
)
_STEAM_COMMON_CANDIDATES = (
    str(Path('~') / '.steam' / 'steam' / 'steamapps' / 'common'),
    str(Path('~') / '.local' / 'share' / 'Steam' / 'steamapps' / 'common'),
    str(Path('~') / '.steam' / 'root' / 'steamapps' / 'common'),
)
_COMPAT_TOOLS_DIR = '~/.local/share/Steam/compatibilitytools.d'


class UbisoftBinaryResolver:
    """Ubisoft binary resolver."""

    def __init__(
        self, config: UbisoftConfig, plugin_dir: str | None,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._plugin_dir = plugin_dir

    def find_umu_run(self) -> str | None:
        """Find UMU run."""
        if self._plugin_dir:
            bundled = os.path.join(
                self._plugin_dir, 'bin', 'umu', 'umu', 'umu-run',
            )
            if os.path.exists(bundled):
                return bundled
        for path in (
            os.path.expanduser(
                '~/.local/share/unifideck/bin/umu/umu/umu-run',
            ),
            '/usr/bin/umu-run',
        ):
            if os.path.exists(path):
                return path
        logger.warning('[Ubisoft] umu-run not found')
        return None

    def find_proton_path(self) -> str | None:
        """Find PROTON path."""
        official = self._find_official_proton()
        if official:
            return official
        return self._find_custom_proton()

    @staticmethod
    def _find_official_proton() -> str | None:
        """Find official PROTON."""
        for steam_common in _STEAM_COMMON_CANDIDATES:
            base = os.path.expanduser(steam_common)
            for name in _PROTON_OFFICIAL_NAMES:
                candidate = os.path.join(base, name)
                if os.path.isdir(candidate):
                    logger.info('[Ubisoft] Using Proton: %s', name)
                    return candidate
        return None

    @staticmethod
    def _find_custom_proton() -> str | None:
        """Find custom PROTON."""
        compat_dir = os.path.expanduser(_COMPAT_TOOLS_DIR)
        if not os.path.isdir(compat_dir):
            logger.warning('[Ubisoft] No Proton in compatibilitytools.d')
            return None
        umu: list[str] = []
        ge: list[str] = []
        try:
            entries = sorted(os.listdir(compat_dir), reverse=True)
        except OSError:
            return None
        for entry in entries:
            full = os.path.join(compat_dir, entry)
            if not os.path.isdir(full):
                continue
            if entry.startswith('UMU-Proton'):
                umu.append(full)
            elif entry.startswith('GE-Proton'):
                ge.append(full)
        candidates = umu + ge
        if candidates:
            logger.info(
                '[Ubisoft] Using Proton: %s', os.path.basename(candidates[0]),
            )
            return candidates[0]
        logger.warning('[Ubisoft] No Proton found')
        return None

    @staticmethod
    def find_python() -> str:
        """Find python."""
        for name in ('python3', 'python'):
            path = shutil.which(name)
            if path:
                return path
        return 'python3'

    @staticmethod
    def proton_family(version_str: str) -> str:
        """Proton family — coarse classification used by callers when
        deciding which compatibility tweaks to apply.
        """
        if not version_str:
            return 'unknown'
        v = version_str.strip()
        if v.startswith('UMU-Proton'):
            return 'umu'
        if v.startswith('GE-Proton'):
            return 'ge'
        if 'Experimental' in v:
            return 'experimental'
        if v.startswith('Proton '):
            return 'official'
        return 'unknown'

    def build_umu_env(
        self,
        wineprefix: str,
        gameid: str,
        *,
        proton_path: str | None = None,
        store_game_id: str | None = None,
        steam_window_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build UMU env."""
        home = os.environ.get('HOME', os.path.expanduser('~'))
        uid = os.getuid()
        env: dict[str, str] = {
            'HOME': home,
            'USER': os.environ.get('USER', 'deck'),
            'PATH': os.environ.get(
                'PATH', '/usr/local/bin:/usr/bin:/bin',
            ),
            'LANG': os.environ.get('LANG', 'en_US.UTF-8'),
            'XDG_RUNTIME_DIR': os.environ.get(
                'XDG_RUNTIME_DIR', f'/run/user/{uid}',
            ),
            'XDG_DATA_HOME': os.environ.get(
                'XDG_DATA_HOME', os.path.join(home, '.local', 'share'),
            ),
            'WINEPREFIX': wineprefix,
            'GAMEID': gameid,
            'STORE': 'ubisoft',
            'PROTON_VERB': 'waitforexitandrun',
        }
        if proton_path:
            env['PROTONPATH'] = proton_path
        if steam_window_env:
            env.update(steam_window_env)
        env.update(self.detect_display_env())
        return env

    def detect_display_env(self) -> dict[str, str]:
        """Detect display env."""
        result = self._collect_display_env_from_self()
        if result.get('DISPLAY') or result.get('WAYLAND_DISPLAY'):
            return result
        if self._fill_display_env_from_steam(result):
            return result
        self._apply_steam_deck_defaults(result)
        return result

    @staticmethod
    def _collect_display_env_from_self() -> dict[str, str]:
        """Collect display env from self (the current process)."""
        result: dict[str, str] = {}
        for var in _DISPLAY_ENV_VARS:
            value = os.environ.get(var)
            if value:
                result[var] = value
        return result

    def _fill_display_env_from_steam(self, result: dict[str, str]) -> bool:
        """Fill display env from steam — returns True when DISPLAY found."""
        for proc_name in ('steam', 'gamescope-session'):
            for pid in self._pgrep(proc_name):
                if self._scan_pid_for_display(pid, result):
                    return True
        return False

    def _scan_pid_for_display(
        self, pid: str, result: dict[str, str],
    ) -> bool:
        """Scan pid for display env."""
        env = self._read_proc_environ(pid, _DISPLAY_ENV_VARS)
        for k, v in env.items():
            if k not in result and v:
                result[k] = v
        if result.get('DISPLAY') or result.get('WAYLAND_DISPLAY'):
            logger.info(
                '[Ubisoft] display env from PID %s: DISPLAY=%s',
                pid, result.get('DISPLAY'),
            )
            return True
        return False

    @staticmethod
    def _apply_steam_deck_defaults(result: dict[str, str]) -> None:
        """Apply steam DECK defaults."""
        if not result.get('DISPLAY'):
            result['DISPLAY'] = ':0'
        xauth = os.path.join(os.path.expanduser('~'), '.Xauthority')
        if not result.get('XAUTHORITY') and os.path.isfile(xauth):
            result['XAUTHORITY'] = xauth

    @staticmethod
    def _pgrep(process_name: str) -> list[str]:
        """Pgrep."""
        try:
            res = subprocess.run(
                ['pgrep', '-u', str(os.getuid()), '-x', process_name],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return [
            line.strip() for line in res.stdout.splitlines() if line.strip()
        ]

    @staticmethod
    def _read_proc_environ(
        pid: str, targets: tuple[str, ...],
    ) -> dict[str, str]:
        """Read proc environ."""
        out: dict[str, str] = {}
        try:
            with open(f'/proc/{pid}/environ', 'rb') as f:
                blob = f.read()
        except (OSError, PermissionError):
            return out
        for entry in blob.split(b'\x00'):
            decoded = entry.decode('utf-8', errors='replace')
            if '=' not in decoded:
                continue
            key, _, value = decoded.partition('=')
            if key in targets and value:
                out[key] = value
        return out
