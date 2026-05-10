"""helpers.py — Wine prefix manipulation primitives.

# OP-59b | py_modules/unifideck/stores/ubisoft/prefix/helpers.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import UbisoftPrefixManager

logger = logging.getLogger(__name__)
_SILENT_INSTALL_FLAG = '/S'


class _PrefixHelpers:
    """Prefix helpers."""

    def __init__(self, parent: UbisoftPrefixManager) -> None:
        """Initialize the instance."""
        self._parent = parent

    async def clone_prefix_from_template(
        self, space_id: str, prefix_path: str,
    ) -> bool:
        """Clone prefix from template."""
        config = self._parent._config
        template = config.template_dir_expanded
        if not os.path.isdir(template):
            return False
        try:
            await self.rsync_clone(template, prefix_path, exclude_games=True)
        except Exception as e:
            logger.warning('[Ubisoft.prefix] clone failed: %s', e)
            return False
        self.write_bootstrap_marker(prefix_path, source='template', space_id=space_id)
        self.fix_pfx_symlink(prefix_path)
        return True

    async def create_prefix_from_fresh_install(
        self, space_id: str, prefix_path: str,
    ) -> bool:
        """Create prefix from fresh install."""
        installer_cache = self._parent._installer_cache
        installer_path = await installer_cache.ensure_cached()
        if not installer_path:
            return False
        os.makedirs(prefix_path, exist_ok=True)
        ok = await self.run_silent_installer(
            prefix_dir=prefix_path,
            installer_path=installer_path,
            gameid=f'umu-ubisoft-{space_id}',
            store_game_id=f'ubisoft:{space_id}',
        )
        if not ok:
            return False
        self.write_bootstrap_marker(prefix_path, source='fresh', space_id=space_id)
        self.fix_pfx_symlink(prefix_path)
        return True

    async def create_template_from_game_prefix(self, game_prefix: str) -> None:
        """Create template from game prefix."""
        config = self._parent._config
        template = config.template_dir_expanded
        try:
            if os.path.isdir(template):
                shutil.rmtree(template, ignore_errors=True)
            await self.rsync_clone(game_prefix, template, exclude_games=True)
            self.write_bootstrap_marker(template, source='derived_from_game', space_id=None)
        except Exception as e:
            logger.warning('[Ubisoft.prefix] template derivation failed: %s', e)

    async def run_silent_installer(
        self, *, prefix_dir: str, installer_path: str, gameid: str,
        store_game_id: str | None = None,
    ) -> bool:
        """Run silent installer."""
        binaries = self._parent._binaries
        umu_run = binaries.find_umu_run()
        if not umu_run:
            return False
        proton = binaries.find_proton_path()
        env = binaries.build_umu_env(
            wineprefix=prefix_dir,
            gameid=gameid,
            proton_path=proton,
            store_game_id=store_game_id,
        )
        cmd = [umu_run, installer_path, _SILENT_INSTALL_FLAG]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as e:
            logger.warning('[Ubisoft.prefix] installer spawn failed: %s', e)
            return False
        return await self._await_installer_completion(proc)

    @staticmethod
    async def _await_installer_completion(
        proc: asyncio.subprocess.Process,
    ) -> bool:
        """Await installer completion."""
        try:
            await asyncio.wait_for(proc.wait(), timeout=30 * 60)
        except TimeoutError:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            return False
        return proc.returncode == 0

    async def rsync_clone(
        self, src: str, dst: str, *, exclude_games: bool,
    ) -> bool:
        """Rsync clone."""
        cmd = ['rsync', '-a', '--delete']
        if exclude_games:
            cmd += [
                '--exclude',
                'drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/games/',
            ]
        cmd += [src.rstrip('/') + '/', dst.rstrip('/') + '/']
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        except OSError as e:
            logger.warning('[Ubisoft.prefix] rsync spawn failed: %s', e)
            return False
        if proc.returncode != 0:
            logger.warning(
                '[Ubisoft.prefix] rsync rc=%s err=%s',
                proc.returncode, stderr[:200].decode('utf-8', errors='replace'),
            )
            return False
        return True

    @staticmethod
    def fix_pfx_symlink(prefix_dir: str) -> None:
        """Fix pfx symlink — Proton expects ``<prefix>/pfx`` to exist
        as a symlink back to the prefix root for sub-tools that rely
        on it. Many bare-Wine prefixes don't have it.
        """
        pfx = os.path.join(prefix_dir, 'pfx')
        if os.path.islink(pfx) or os.path.isdir(pfx):
            return
        try:
            os.symlink('.', pfx, target_is_directory=True)
        except OSError as e:
            logger.debug('[Ubisoft.prefix] pfx symlink: %s', e)

    def write_bootstrap_marker(
        self, prefix_dir: str, source: str, space_id: str | None,
    ) -> None:
        """Write bootstrap marker."""
        config = self._parent._config
        marker_path = os.path.join(prefix_dir, config.bootstrap_marker)
        payload = {
            'created_at': datetime.datetime.utcnow().isoformat() + 'Z',
            'source': source,
            'space_id': space_id or '',
        }
        try:
            os.makedirs(os.path.dirname(marker_path), exist_ok=True)
            with open(marker_path, 'w', encoding='utf-8') as f:
                for key, value in payload.items():
                    f.write(f'{key}={value}\n')
        except OSError as e:
            logger.debug('[Ubisoft.prefix] bootstrap marker: %s', e)

    def try_inject_auth_state(self, prefix_paths: list[str]) -> None:
        """Try inject auth state."""
        try:
            self._parent._inject_auth_state(prefix_paths)
        except Exception as e:
            logger.debug('[Ubisoft.prefix] inject auth state failed: %s', e)
