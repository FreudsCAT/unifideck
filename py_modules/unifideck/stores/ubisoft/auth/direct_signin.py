"""direct_signin.py — Headless UPC sign-in (no Steam shortcut required).

# OP-58e | py_modules/unifideck/stores/ubisoft/auth/direct_signin.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ....security import emit_external_auth_check_failed
from ..binaries import UbisoftBinaryResolver
from ..paths import UbisoftPrefixPaths
from ..session import UbisoftSession

logger = logging.getLogger(__name__)


class _DirectSignIn:
    """Direct sign in."""

    def __init__(
        self,
        *,
        binaries: UbisoftBinaryResolver,
        bus: Any,
        config: Any,
        paths: UbisoftPrefixPaths,
        session: UbisoftSession,
        ensure_auth_prefix: Any,
        queue_auth_assets_ensure: Any,
    ) -> None:
        """Initialize the instance."""
        self._binaries = binaries
        self._bus = bus
        self._config = config
        self._paths = paths
        self._session = session
        self._ensure_auth_prefix = ensure_auth_prefix
        self._queue = queue_auth_assets_ensure

    async def connect(self) -> dict[str, Any]:
        """Connect."""
        targets = await self._resolve_launch_targets()
        if isinstance(targets, dict):
            return targets
        prefix_path, upc_path, umu_run = targets
        env_pair = self._build_launch_env(prefix_path)
        if env_pair is None:
            return {'success': False, 'error': 'launch_env_unavailable'}
        python_bin, env = env_pair
        env['STEAM_COMPAT_DATA_PATH'] = prefix_path
        cmd = [umu_run, upc_path]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            await emit_external_auth_check_failed(
                self._bus, store='ubisoft', reason=str(e),
            )
            return {'success': False, 'error': f'spawn_failed:{e}'}
        captured = await self._wait_for_capture(proc, prefix_path)
        if not captured:
            return {'success': False, 'error': 'capture_timeout'}
        return self._finalize_success(prefix_path)

    async def _resolve_launch_targets(
        self,
    ) -> tuple[str, str, str] | dict[str, Any]:
        """Resolve launch targets."""
        prefix_path = await self._ensure_auth_prefix()
        if not prefix_path:
            return {'success': False, 'error': 'auth_prefix_unavailable'}
        upc_path = self._paths.find_upc_exe(prefix_path)
        if not upc_path:
            return {'success': False, 'error': 'upc_exe_not_found'}
        umu_run = self._binaries.find_umu_run()
        if not umu_run:
            return {'success': False, 'error': 'umu_run_not_found'}
        return prefix_path, upc_path, umu_run

    def _build_launch_env(
        self, prefix_path: str,
    ) -> tuple[str, dict[str, str]] | None:
        """Build launch env."""
        python_bin = self._binaries.find_python()
        if not python_bin:
            return None
        proton_path = self._binaries.find_proton_path()
        env = self._binaries.build_umu_env(
            wineprefix=prefix_path,
            gameid='umu-ubisoft-auth',
            proton_path=proton_path,
        )
        return python_bin, env

    def _finalize_success(self, prefix_path: str) -> dict[str, Any]:
        """Finalize success."""
        captured = self._session.capture(prefix_path)
        self._queue('direct_signin')
        return {
            'success': True,
            'prefix_path': prefix_path,
            'session_token_present': bool(captured),
        }

    async def _wait_for_capture(
        self, proc: asyncio.subprocess.Process, prefix_path: str,
    ) -> str | None:
        """Wait for capture."""
        deadline_s = 30 * 60
        elapsed = 0.0
        interval = 2.0
        while elapsed < deadline_s:
            if self._session.has_valid_credentials(prefix_path):
                token = self._session.capture(prefix_path)
                if proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                return token or 'captured'
            if proc.returncode is not None:
                logger.info(
                    '[Ubisoft.auth] UPC exited rc=%s', proc.returncode,
                )
                return None
            await asyncio.sleep(interval)
            elapsed += interval
        return None
