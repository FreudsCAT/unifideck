"""
Auth prefix builder — variant of template_builder for the auth flow.

OP-59d | py_modules/unifideck/stores/ubisoft/prefix/auth_builder.py

The auth prefix has different requirements from the template prefix:
it must allow the UPC GUI to come up and the user to sign in
interactively, whereas the template prefix runs UPC headlessly. This
class is the auth-flow-specific build path that produces the
``.upc-auth`` prefix.

Largely shares the underlying steps with ``_TemplateBuilder`` but
configures the prefix with display-aware overrides (DXVK enabled,
display server connected) and leaves UPC running at the end so the
user can sign in.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unifideck.stores.ubisoft.config import UbisoftConfig
    from unifideck.stores.ubisoft.installer.cache import UbisoftInstallerCache
    from unifideck.stores.ubisoft.paths import UbisoftPrefixPaths

    from .helpers import _PrefixHelpers
    from .template_builder import _TemplatePrefixBuilder
logger = logging.getLogger(__name__)


class _AuthPrefixBuilder:
    """Auth prefix builder."""

    def __init__(
        self,
        *,
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
        self._auth_assets_task: asyncio.Task[None] | None = None
        self._auth_assets_lock = asyncio.Lock()

    async def ensure_auth_prefix(self) -> str | None:
        """Ensure auth prefix."""
        auth_dir = self._config.auth_prefix_dir_expanded
        upc_path = self._paths.find_upc_exe(auth_dir)
        rebuild = self._auth_prefix_needs_rebuild(
            auth_dir,
            upc_path,
        )
        if not rebuild and await asyncio.to_thread(lambda: Path(auth_dir).is_dir()) and not upc_path:
            logger.warning(
                "[UbisoftPrefixManager] auth prefix exists "
                "but upc.exe missing, re-cloning",
            )
            shutil.rmtree(auth_dir, ignore_errors=True)
            upc_path = None
            rebuild = True
        if upc_path and not rebuild:
            return upc_path
        return await self._rebuild_and_finalise_auth_prefix(
            auth_dir,
        )

    def _auth_prefix_needs_rebuild(
        self,
        auth_dir: str,
        upc_path: str | None,
    ) -> bool:
        """Auth prefix needs rebuild."""
        if not upc_path:
            return False
        if self._template_builder.is_prefix_version_stale(auth_dir):
            logger.warning(
                "[UbisoftPrefixManager] auth prefix Proton version stale, rebuilding",
            )
            return True
        if self._template_builder.template_exists():
            auth_guid = self._template_builder.read_machine_guid(
                auth_dir,
            )
            tmpl_guid = self._template_builder.read_machine_guid(
                self._config.template_dir_expanded,
            )
            if tmpl_guid and auth_guid and tmpl_guid != auth_guid:
                logger.warning(
                    "[UbisoftPrefixManager] auth prefix "
                    "MachineGuid desynced from template — "
                    "rebuilding for DPAPI compatibility",
                )
                return True
        return False

    async def _rebuild_and_finalise_auth_prefix(
        self,
        auth_dir: str,
    ) -> str | None:
        """Rebuild and finalise auth prefix."""
        await self._template_builder.regenerate_template_if_stale()
        cloned = await self._build_auth_prefix_from_source()
        if not cloned:
            return None
        self._helpers.fix_pfx_symlink(auth_dir)
        upc_path = self._paths.find_upc_exe(auth_dir)
        if upc_path:
            self._helpers.try_inject_auth_state([auth_dir])
            logger.info(
                "[UbisoftPrefixManager] auth prefix ready",
            )
            return upc_path
        return None

    async def _build_auth_prefix_from_source(self) -> bool:
        """Build auth prefix from source."""
        auth_dir = self._config.auth_prefix_dir_expanded
        src, label = self._pick_clone_source()
        if src:
            logger.info(
                "[UbisoftPrefixManager] cloning %s → auth prefix",
                label,
            )
            await asyncio.to_thread(lambda: Path(auth_dir).mkdir(parents=True, exist_ok=True))
            ok = await self._helpers.rsync_clone(
                src,
                auth_dir,
                exclude_games=True,
            )
            if not ok:
                logger.error(
                    "[UbisoftPrefixManager] rsync clone failed for auth prefix",
                )
                return False
            return True
        logger.info(
            "[UbisoftPrefixManager] no template/game prefix — "
            "bootstrapping auth prefix via fresh install",
        )
        installer_path = await self._installer_cache.ensure_cached()
        if not installer_path:
            return False
        await asyncio.to_thread(lambda: Path(auth_dir).mkdir(parents=True, exist_ok=True))
        success = await self._helpers.run_silent_installer(
            prefix_dir=auth_dir,
            installer_path=installer_path,
            gameid="umu-ubisoft-auth",
            store_game_id=self._config.auth_shortcut_store_id,
        )
        if not success and not self._paths.find_upc_exe(auth_dir):
            return False
        self._helpers.write_bootstrap_marker(
            auth_dir,
            "auth_prefix",
            None,
        )
        return True

    def _pick_clone_source(self) -> tuple[str | None, str]:
        """Pick clone source."""
        if self._template_builder.template_exists():
            return (
                self._config.template_dir_expanded,
                "template",
            )
        prefixes_dir = self._config.prefixes_dir_expanded
        if not Path(prefixes_dir).is_dir():
            return (None, "")
        try:
            entries = sorted([entry.name for entry in Path(prefixes_dir).iterdir()])
        except OSError:
            return (None, "")
        for entry in entries:
            if entry.startswith("."):
                continue
            candidate = str(Path(prefixes_dir) / entry)
            if self._paths.find_upc_exe(candidate):
                return (candidate, f"game prefix {entry[:8]}")
        return (None, "")

    def queue_auth_assets_ensure(
        self,
        reason: str = "background",
    ) -> None:
        """Queue auth assets ensure."""
        if self._auth_assets_task is not None and not self._auth_assets_task.done():
            logger.info(
                "[UbisoftPrefixManager] auth asset ensure "
                "already in progress (reason=%s)",
                reason,
            )
            return
        logger.info(
            "[UbisoftPrefixManager] queueing auth asset ensure (reason=%s)",
            reason,
        )
        self._auth_assets_task = asyncio.create_task(
            self._ensure_auth_assets(reason),
        )

    async def _ensure_auth_assets(self, reason: str) -> None:
        """Ensure auth assets."""
        async with self._auth_assets_lock:
            logger.info(
                "[UbisoftPrefixManager] ensuring auth assets (reason=%s)",
                reason,
            )
            await self._template_builder.regenerate_template_if_stale()
            if not self._template_builder.template_exists():
                await self._template_builder.ensure_template_prefix()
                return
            template_dir = self._config.template_dir_expanded
            self._helpers.try_inject_auth_state([template_dir])
            await self._repair_auth_prefix_if_needed()

    async def _repair_auth_prefix_if_needed(self) -> None:
        """Repair auth prefix if needed."""
        auth_dir = self._config.auth_prefix_dir_expanded
        session_file = self._config.upc_session_file_expanded
        if await asyncio.to_thread(lambda: Path(auth_dir).is_dir()):
            self._helpers.try_inject_auth_state([auth_dir])
            return
        if not await asyncio.to_thread(lambda: Path(session_file).is_file()):
            return
        logger.info(
            "[UbisoftPrefixManager] auth prefix "
            "missing but user is authenticated; "
            "recreating",
        )
        await self.ensure_auth_prefix()
        game_prefixes = list(self._config.iter_game_prefix_paths())
        if game_prefixes:
            self._helpers.try_inject_auth_state(game_prefixes)
