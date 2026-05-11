"""installer.py — Public ``GOGInstaller`` orchestrator.

# OP-51a | py_modules/unifideck/stores/gog/install/installer.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from ....core.types import InstallResult, Result
from ..config import GOGConfig
from ..tokens import GOGTokenManager
from .helpers import _InstallHelpers
from .marker import _PostInstallMarker
from .planner import GOGInstallPlanner
from .progress import _GogdlProgressMonitor
from .uninstall_pipeline import _UninstallPipeline

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class _InstallContext:
    """Install context."""

    game_id: str
    base_path: str
    preferred_lang: str
    explicit_lang: bool
    progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None
    platform: str = ''
    folder_name: str | None = None
    supported_langs: list[str] = field(default_factory=list)
    existing_dirs: set = field(default_factory=set)
    support_dir: str = ''
    install_mode: str = ''
    found_path: str = ''


class GOGInstaller:
    """GOG installer."""

    def __init__(
        self,
        config: GOGConfig,
        tokens: GOGTokenManager,
        gogdl_bin: str,
        exe_finder: Callable[[str], str | None],
        locale_fn: Callable[[], str],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._tokens = tokens
        self._gogdl_bin = gogdl_bin
        self._exe_finder = exe_finder
        self._locale_fn = locale_fn
        self._planner = GOGInstallPlanner(config, tokens)
        self._planner.set_gogdl_bin(gogdl_bin)
        self._helpers = _InstallHelpers(self)
        self._marker = _PostInstallMarker(self)
        self._progress = _GogdlProgressMonitor(self)
        self._uninstall = _UninstallPipeline(self)

    async def uninstall_game(
        self, game_id: str, install_path: str | None = None,
    ) -> Result:
        """Uninstall game."""
        result = await self._uninstall.uninstall_game(game_id, install_path)
        await self._wipe_manifests(game_id)
        await self._wipe_support_cache(game_id)
        return result

    async def _run_gogdl_with_progress(
        self,
        install_mode: str,
        game_id: str,
        platform: str,
        path: str,
        support_dir: str,
        languages: list[str],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> bool:
        """Run GOGDL with progress."""
        return await self._progress.run_gogdl_with_progress(
            install_mode, game_id, platform, path,
            support_dir, languages, progress_cb,
        )

    async def _run_gogdl_repair_pass(
        self,
        game_id: str,
        platform: str,
        base_path: str,
        folder_name: str | None,
        preferred_lang: str,
    ) -> None:
        """Run GOGDL repair pass."""
        await self._progress.run_gogdl_repair_pass(
            game_id, platform, base_path, folder_name, preferred_lang,
        )

    def _snapshot_dirs(self, base_path: str) -> set:
        """Snapshot dirs."""
        return self._marker.snapshot_dirs(base_path)

    async def _locate_install(
        self,
        game_id: str,
        base_path: str,
        folder_name: str | None,
        existing_dirs: set,
    ) -> str | None:
        """Locate install."""
        return await self._marker.locate_install(
            game_id, base_path, folder_name, existing_dirs,
        )

    async def _write_install_marker(
        self, install_path: str, game_id: str, language: str,
    ) -> bool:
        """Write install marker."""
        return await self._marker.write_install_marker(
            install_path, game_id, language,
        )

    async def _regenerate_manifest(
        self, game_id: str, platform: str,
    ) -> None:
        """Regenerate manifest."""
        await self._marker.regenerate_manifest(game_id, platform)

    def _install_failed(
        self,
        game_id: str,
        error: str,
        *,
        cleanup_path: str | None = None,
        cleanup_folder: str | None = None,
    ) -> InstallResult:
        """Install failed."""
        if cleanup_path:
            self._cleanup_partial(cleanup_path, cleanup_folder)
        return InstallResult(
            success=False, store='gog', game_id=game_id, error=error,
        )

    async def install_game(
        self,
        game_id: str,
        base_path: str | None = None,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        language: str | None = None,
    ) -> InstallResult:
        """Install game."""
        ctx = self._install_preflight(game_id, base_path, progress_cb, language)
        if not ctx[1]:
            return cast(InstallResult, ctx[2])
        context = ctx[2]
        prepared = await self._install_probe_and_prepare(context)
        if prepared is not None:
            return prepared
        ran = await self._install_run_gogdl_phase(context)
        if ran is not None:
            return ran
        return await self._install_finalize(context)

    def _install_preflight(
        self,
        game_id: str,
        base_path: str | None,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
        language: str | None,
    ) -> tuple:
        """Install preflight."""
        if not self._gogdl_bin:
            return (
                game_id, False,
                self._install_failed(game_id, 'gogdl_not_found'),
            )
        path = base_path or os.path.expanduser(self._config.download_dir)
        os.makedirs(path, exist_ok=True)
        explicit = bool(language)
        primary = language or self._locale_fn() or 'en-US'
        context = _InstallContext(
            game_id=game_id, base_path=path,
            preferred_lang=primary, explicit_lang=explicit,
            progress_cb=progress_cb,
        )
        return game_id, True, context

    async def _install_probe_and_prepare(
        self, ctx: _InstallContext,
    ) -> InstallResult | None:
        """Install probe and prepare."""
        if not await self._tokens.refresh_if_stale():
            return self._install_failed(ctx.game_id, 'no_tokens')
        platform, folder, langs = await self._helpers.probe_game_info(
            ctx.game_id,
        )
        ctx.platform = platform
        ctx.folder_name = folder
        ctx.supported_langs = langs
        ctx.existing_dirs = self._snapshot_dirs(ctx.base_path)
        target = (
            os.path.join(ctx.base_path, folder) if folder else None
        )
        ctx.install_mode = await self._planner.determine_install_mode(
            ctx.game_id, target,
        )
        return None

    async def _install_run_gogdl_phase(
        self, ctx: _InstallContext,
    ) -> InstallResult | None:
        """Install run GOGDL phase."""
        ctx.support_dir = os.path.join(
            os.path.expanduser(self._config.gogdl_config_dir),
            'support', ctx.game_id,
        )
        languages = self._helpers.pick_languages(
            ctx.preferred_lang, ctx.explicit_lang, ctx.supported_langs,
        )
        ok = await self._run_gogdl_with_progress(
            ctx.install_mode, ctx.game_id, ctx.platform,
            ctx.base_path, ctx.support_dir, languages, ctx.progress_cb,
        )
        if not ok:
            return self._install_failed(
                ctx.game_id, 'gogdl_failed',
                cleanup_path=ctx.base_path,
                cleanup_folder=ctx.folder_name,
            )
        return None

    async def _install_finalize(
        self, ctx: _InstallContext,
    ) -> InstallResult:
        """Install finalize."""
        install_path = await self._locate_install(
            ctx.game_id, ctx.base_path, ctx.folder_name,
            ctx.existing_dirs,
        )
        if not install_path:
            return self._install_failed(
                ctx.game_id, 'install_dir_not_detected',
                cleanup_path=ctx.base_path,
                cleanup_folder=ctx.folder_name,
            )
        verification = await self._planner.verify_installation(
            ctx.game_id, install_path, ctx.platform, self._exe_finder,
        )
        if not verification['success']:
            await self._run_gogdl_repair_pass(
                ctx.game_id, ctx.platform, ctx.base_path,
                ctx.folder_name, ctx.preferred_lang,
            )
        await self._write_install_marker(
            install_path, ctx.game_id, ctx.preferred_lang,
        )
        await self._regenerate_manifest(ctx.game_id, ctx.platform)
        return InstallResult(
            success=True, store='gog', game_id=ctx.game_id,
            install_path=install_path,
        )

    async def _wipe_manifests(self, game_id: str) -> None:
        """Wipe manifests."""
        for path in self._planner.manifest_locations(game_id):
            try:
                if os.path.isfile(path):
                    os.unlink(path)
                elif os.path.isdir(path):
                    await asyncio.to_thread(
                        shutil.rmtree, path, ignore_errors=True,
                    )
            except OSError as e:
                logger.debug('[GOGInstaller] manifest wipe %s: %s', path, e)

    async def _wipe_support_cache(self, game_id: str) -> None:
        """Wipe support cache."""
        path = os.path.join(
            os.path.expanduser(self._config.gogdl_config_dir),
            'support', game_id,
        )
        if os.path.isdir(path):
            await asyncio.to_thread(
                shutil.rmtree, path, ignore_errors=True,
            )

    def _cleanup_partial(
        self, base_path: str, folder_name: str | None,
    ) -> None:
        """Cleanup partial."""
        if not folder_name:
            return
        path = os.path.join(base_path, folder_name)
        if os.path.isdir(path):
            try:
                shutil.rmtree(path, ignore_errors=True)
            except OSError as e:
                logger.debug('[GOGInstaller] cleanup %s: %s', path, e)
