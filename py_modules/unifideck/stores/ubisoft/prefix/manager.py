"""manager.py — Public ``UbisoftPrefixManager`` surface.

# OP-59a | py_modules/unifideck/stores/ubisoft/prefix/manager.py | Depends: (none)
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from ..binaries import UbisoftBinaryResolver
from ..config import UbisoftConfig
from ..installer.cache import UbisoftInstallerCache
from ..paths import UbisoftPrefixPaths
from .auth_builder import _AuthPrefixBuilder
from .helpers import _PrefixHelpers
from .template_builder import _TemplatePrefixBuilder

logger = logging.getLogger(__name__)


class UbisoftPrefixManager:
    """Ubisoft prefix manager."""

    def __init__(
        self,
        config: UbisoftConfig,
        paths: UbisoftPrefixPaths,
        binaries: UbisoftBinaryResolver,
        installer_cache: UbisoftInstallerCache,
        inject_auth_state: Callable[[list[str]], int],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths
        self._binaries = binaries
        self._installer_cache = installer_cache
        self._inject_auth_state = inject_auth_state
        self._helpers = _PrefixHelpers(self)
        self._template_builder = _TemplatePrefixBuilder(
            config=config, paths=paths,
            helpers=self._helpers, installer_cache=installer_cache,
        )
        self._auth_builder = _AuthPrefixBuilder(
            config=config, paths=paths,
            helpers=self._helpers, installer_cache=installer_cache,
            template_builder=self._template_builder,
        )

    def template_exists(self) -> bool:
        """Template exists."""
        return self._template_builder.template_exists()

    def is_prefix_version_stale(self, prefix_dir: str) -> bool:
        """Is prefix version stale."""
        return self._template_builder.is_prefix_version_stale(prefix_dir)

    @staticmethod
    def read_machine_guid(prefix_path: str) -> str:
        """Read machine guid."""
        return _TemplatePrefixBuilder.read_machine_guid(prefix_path)

    def queue_template_creation(self) -> None:
        """Queue template creation."""
        self._template_builder.queue_template_creation()

    async def regenerate_template_if_stale(self) -> None:
        """Regenerate template if stale."""
        await self._template_builder.regenerate_template_if_stale()

    async def ensure_template_prefix(self) -> None:
        """Ensure template prefix."""
        await self._template_builder.ensure_template_prefix()

    async def ensure_auth_prefix(self) -> str | None:
        """Ensure auth prefix."""
        return await self._auth_builder.ensure_auth_prefix()

    def queue_auth_assets_ensure(self, reason: str = 'background') -> None:
        """Queue auth assets ensure."""
        self._auth_builder.queue_auth_assets_ensure(reason)

    async def bootstrap_game_prefix(self, space_id: str) -> bool:
        """Bootstrap game prefix."""
        prefix_path = self._paths.get_prefix_path(space_id)
        if self._paths.find_upc_exe(prefix_path):
            self._helpers.fix_pfx_symlink(prefix_path)
            return True
        if (
            self._template_builder.template_exists()
            and not self._template_builder.is_prefix_version_stale(
                self._config.template_dir_expanded,
            )
        ):
            ok = await self._helpers.clone_prefix_from_template(
                space_id, prefix_path,
            )
            if ok:
                self._inject_auth_state([prefix_path])
                return True
        ok = await self._helpers.create_prefix_from_fresh_install(
            space_id, prefix_path,
        )
        if ok:
            self._inject_auth_state([prefix_path])
        return ok

    async def repair_prefix(self, space_id: str) -> bool:
        """Repair prefix."""
        prefix_path = self._paths.get_prefix_path(space_id)
        if Path(prefix_path).is_dir():
            shutil.rmtree(prefix_path, ignore_errors=True)
        return await self.bootstrap_game_prefix(space_id)
