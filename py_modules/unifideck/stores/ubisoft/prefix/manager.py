"""
Wine prefix lifecycle manager for Ubisoft games.

OP-59a | py_modules/unifideck/stores/ubisoft/prefix/manager.py

``UbisoftPrefixManager`` owns the creation, validation, and destruction
of Wine prefixes used by Ubisoft games. Three categories of prefix
coexist:

1. **template prefix** (``.template``) — UPC-installed-but-no-game;
   used as the base for fresh installs (avoid running the UPC installer
   for every game).
2. **auth prefix** (``.upc-auth``) — used solely by the auth flow.
3. **per-game prefixes** — one per installed game.

The manager exposes ``ensure_template``, ``ensure_auth``, ``create_for_game``,
``destroy``, and ``validate``. Each operation is delegated to one of
``template_builder.py`` / ``auth_builder.py`` / ``helpers.py``.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from unifideck.stores.ubisoft.binaries import UbisoftBinaryResolver
from unifideck.stores.ubisoft.config import UbisoftConfig
from unifideck.stores.ubisoft.installer.cache import UbisoftInstallerCache
from unifideck.stores.ubisoft.paths import UbisoftPrefixPaths

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
            config=config,
            paths=paths,
            helpers=self._helpers,
            installer_cache=installer_cache,
        )
        self._auth_builder = _AuthPrefixBuilder(
            config=config,
            paths=paths,
            helpers=self._helpers,
            installer_cache=installer_cache,
            template_builder=self._template_builder,
        )

    def template_exists(self) -> bool:
        """Template exists."""
        return self._template_builder.template_exists()

    def is_prefix_version_stale(self, prefix_dir: str) -> bool:
        """Check whether prefix version stale."""
        return self._template_builder.is_prefix_version_stale(
            prefix_dir,
        )

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

    def queue_auth_assets_ensure(
        self,
        reason: str = "background",
    ) -> None:
        """Queue auth assets ensure."""
        self._auth_builder.queue_auth_assets_ensure(reason)

    async def bootstrap_game_prefix(self, space_id: str) -> bool:
        """Bootstrap game prefix."""
        prefix_path = self._paths.get_prefix_path(space_id)
        marker_path = Path(prefix_path) / self._config.bootstrap_marker
        if marker_path.is_file() and self._paths.find_upc_exe(prefix_path):
            self._helpers.try_inject_auth_state([prefix_path])
            return True
        if (
            self._template_builder.template_exists()
            and await self._helpers.clone_prefix_from_template(
                space_id,
                prefix_path,
            )
        ):
            return True
        return await self._helpers.create_prefix_from_fresh_install(
            space_id,
            prefix_path,
        )

    async def repair_prefix(
        self,
        space_id: str,
    ) -> bool:
        """Repair prefix."""
        prefix_path = self._paths.get_prefix_path(space_id)
        logger.info(
            "[UbisoftPrefixManager] repairing prefix for %s",
            space_id,
        )
        try:
            if await asyncio.to_thread(lambda: Path(prefix_path).is_dir()):
                shutil.rmtree(prefix_path)
                logger.info(
                    "[UbisoftPrefixManager] removed corrupted prefix for %s",
                    space_id,
                )
        except OSError:
            logger.exception("[UbisoftPrefixManager] could not remove corrupted prefix")
            return False
        return await self.bootstrap_game_prefix(space_id)
