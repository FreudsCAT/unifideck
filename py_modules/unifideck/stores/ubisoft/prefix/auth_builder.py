"""auth_builder.py — Build / repair the dedicated auth prefix.

# OP-59d | py_modules/unifideck/stores/ubisoft/prefix/auth_builder.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import UbisoftConfig
    from ..installer.cache import UbisoftInstallerCache
    from ..paths import UbisoftPrefixPaths
    from .helpers import _PrefixHelpers
    from .template_builder import _TemplatePrefixBuilder

logger = logging.getLogger(__name__)


class _AuthPrefixBuilder:
    """Auth prefix builder."""

    def __init__(
        self, *,
        config: UbisoftConfig,
        paths: UbisoftPrefixPaths,
        helpers: _PrefixHelpers,
        installer_cache: UbisoftInstallerCache,
        template_builder: _TemplatePrefixBuilder,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths
        self._helpers = helpers
        self._installer_cache = installer_cache
        self._template_builder = template_builder
        self._ensure_lock = asyncio.Lock()

    async def ensure_auth_prefix(self) -> str | None:
        """Ensure auth prefix."""
        async with self._ensure_lock:
            auth_dir = self._config.auth_prefix_dir_expanded
            await self._repair_auth_prefix_if_needed()
            if self._auth_prefix_needs_rebuild(
                auth_dir, self._paths.find_upc_exe(auth_dir),
            ):
                return await self._rebuild_and_finalise_auth_prefix(auth_dir)
            return auth_dir

    def _auth_prefix_needs_rebuild(
        self, auth_dir: str, upc_path: str | None,
    ) -> bool:
        """Auth prefix needs rebuild."""
        if not os.path.isdir(auth_dir) or not upc_path:
            return True
        return self._template_builder.is_prefix_version_stale(auth_dir)

    async def _rebuild_and_finalise_auth_prefix(
        self, auth_dir: str,
    ) -> str | None:
        """Rebuild and finalise auth prefix."""
        if os.path.isdir(auth_dir):
            shutil.rmtree(auth_dir, ignore_errors=True)
        if not await self._build_auth_prefix_from_source():
            return None
        return auth_dir

    async def _build_auth_prefix_from_source(self) -> bool:
        """Build auth prefix from source."""
        auth_dir = self._config.auth_prefix_dir_expanded
        clone_source, source_label = self._pick_clone_source()
        if clone_source and source_label == 'template':
            ok = await self._helpers.clone_prefix_from_template(
                space_id='auth', prefix_path=auth_dir,
            )
            if ok:
                return True
        return await self._helpers.create_prefix_from_fresh_install(
            space_id='auth', prefix_path=auth_dir,
        )

    def _pick_clone_source(self) -> tuple[str | None, str]:
        """Pick clone source."""
        template = self._config.template_dir_expanded
        if (
            self._template_builder.template_exists()
            and not self._template_builder.is_prefix_version_stale(template)
        ):
            return template, 'template'
        for prefix in self._config.iter_game_prefix_paths():
            if (
                self._paths.find_upc_exe(prefix)
                and not self._template_builder.is_prefix_version_stale(prefix)
            ):
                return prefix, 'game_prefix'
        return None, 'fresh'

    def queue_auth_assets_ensure(self, reason: str = 'background') -> None:
        """Queue auth assets ensure."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self._ensure_auth_assets(reason),
            name=f'ubisoft_auth_assets:{reason}',
        )

    async def _ensure_auth_assets(self, reason: str) -> None:
        """Ensure auth assets."""
        try:
            await self.ensure_auth_prefix()
        except Exception as e:
            logger.warning(
                '[Ubisoft.prefix] ensure_auth_assets(%s) failed: %s',
                reason, e,
            )

    async def _repair_auth_prefix_if_needed(self) -> None:
        """Repair auth prefix if needed."""
        auth_dir = self._config.auth_prefix_dir_expanded
        if not os.path.isdir(auth_dir):
            return
        marker = Path(auth_dir) / self._config.bootstrap_marker
        if marker.is_file():
            return
        # Missing marker means an interrupted bootstrap — wipe and rebuild.
        logger.info(
            '[Ubisoft.prefix] auth prefix missing marker, will rebuild',
        )
        shutil.rmtree(auth_dir, ignore_errors=True)
