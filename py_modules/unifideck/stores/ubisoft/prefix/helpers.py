"""
Wine prefix helpers — symlink fixups, marker writing, basic file ops.

OP-59b | py_modules/unifideck/stores/ubisoft/prefix/helpers.py

Helper class with a grab-bag of operations the prefix builders rely on:

* ``fix_pfx_symlink`` — fixes the legacy ``<prefix>/pfx`` symlink some
  Proton versions expect;
* ``write_bootstrap_marker`` — writes the marker file that flags a
  prefix as "Unifideck-managed";
* ``has_bootstrap_marker`` — checks a prefix for the marker;
* misc. ``Path``-based wrappers around create/delete/check operations.

Kept as a separate module so the builders can stay focused on the
high-level construction logic.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import UbisoftPrefixManager
logger = logging.getLogger(__name__)
_SILENT_INSTALL_FLAG = "/S"


class _PrefixHelpers:
    """Prefix helpers."""

    def __init__(self, parent: UbisoftPrefixManager) -> None:
        """Initialize the instance."""
        self._parent = parent

    async def clone_prefix_from_template(
        self,
        space_id: str,
        prefix_path: str,
    ) -> bool:
        """Clone prefix from template."""
        logger.info(
            "[UbisoftPrefixManager] cloning template for %s",
            space_id,
        )
        try:
            os.makedirs(prefix_path, exist_ok=True)
            ok = await self.rsync_clone(
                self._parent._config.template_dir_expanded,
                prefix_path,
                exclude_games=False,
            )
            if not ok:
                logger.error(
                    "[UbisoftPrefixManager] rsync clone failed for %s",
                    space_id,
                )
                return False
            self.write_bootstrap_marker(
                prefix_path,
                "cloned_from_template",
                space_id,
            )
            self.try_inject_auth_state([prefix_path])
            logger.info(
                "[UbisoftPrefixManager] prefix cloned for %s",
                space_id,
            )
            return True
        except Exception:
            logger.exception("[UbisoftPrefixManager] clone failed")
            return False

    async def create_prefix_from_fresh_install(
        self,
        space_id: str,
        prefix_path: str,
    ) -> bool:
        """Create prefix from fresh install."""
        logger.info(
            "[UbisoftPrefixManager] fresh install for %s",
            space_id,
        )
        installer_path = await self._parent._installer_cache.ensure_cached()
        if not installer_path:
            return False
        try:
            os.makedirs(prefix_path, exist_ok=True)
            success = await self.run_silent_installer(
                prefix_dir=prefix_path,
                installer_path=installer_path,
                gameid=f"umu-ubisoft-{space_id}",
                store_game_id=f"ubisoft:{space_id}",
            )
            if not success:
                return False
            if not self._parent._paths.find_upc_exe(prefix_path):
                logger.error(
                    "[UbisoftPrefixManager] upc.exe not "
                    "found after fresh install for %s",
                    space_id,
                )
                return False
            self.write_bootstrap_marker(
                prefix_path,
                "fresh_install",
                space_id,
            )
            self.try_inject_auth_state([prefix_path])
            if not self._parent.template_exists():
                await self.create_template_from_game_prefix(
                    prefix_path,
                )
            return True
        except Exception:
            logger.exception("[UbisoftPrefixManager] fresh install failed for %s", space_id)
            return False

    async def create_template_from_game_prefix(
        self,
        game_prefix: str,
    ) -> None:
        """Create template from game prefix."""
        template_dir = self._parent._config.template_dir_expanded
        logger.info(
            "[UbisoftPrefixManager] creating template from first game prefix",
        )
        try:
            os.makedirs(template_dir, exist_ok=True)
            ok = await self.rsync_clone(
                game_prefix,
                template_dir,
                exclude_games=False,
            )
            if not ok:
                return
            self.write_bootstrap_marker(
                template_dir,
                "template",
                None,
            )
            self.try_inject_auth_state([template_dir])
        except Exception as e:
            logger.warning(
                "[UbisoftPrefixManager] template creation from game prefix failed: %s",
                e,
            )

    async def run_silent_installer(
        self,
        *,
        prefix_dir: str,
        installer_path: str,
        gameid: str,
        store_game_id: str | None = None,
    ) -> bool:
        """Run silent installer."""
        umu_run = self._parent._binaries.find_umu_run()
        if not umu_run:
            logger.error(
                "[UbisoftPrefixManager] umu-run not found",
            )
            return False
        env = self._parent._binaries.build_umu_env(
            wineprefix=prefix_dir,
            gameid=gameid,
            store_game_id=store_game_id,
        )
        python_bin = self._parent._binaries.find_python()
        logger.info(
            "[UbisoftPrefixManager] installer run: PROTONPATH=%s GAMEID=%s",
            env.get("PROTONPATH"),
            env.get("GAMEID"),
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                python_bin,
                umu_run,
                installer_path,
                _SILENT_INSTALL_FLAG,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.exception("[UbisoftPrefixManager] subprocess spawn failed")
            return False
        return await self._await_installer_completion(proc)

    @staticmethod
    async def _await_installer_completion(
        proc: asyncio.subprocess.Process,
    ) -> bool:
        """Await installer completion."""
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=15 * 60,
            )
        except TimeoutError:
            logger.exception(
                "[UbisoftPrefixManager] installer timed out after 15 min — killing",
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return False
        if proc.returncode != 0:
            stderr_text = (
                stderr.decode(
                    errors="replace",
                )[:500]
                if stderr
                else ""
            )
            logger.error(
                "[UbisoftPrefixManager] installer exited %d: %s",
                proc.returncode,
                stderr_text,
            )
            return False
        return True

    async def rsync_clone(
        self,
        src: str,
        dst: str,
        *,
        exclude_games: bool,
    ) -> bool:
        """Rsync clone."""
        args: list[str] = ["rsync", "-a"]
        if exclude_games:
            args.append("--exclude=games")
        args.extend([f"{src}/", f"{dst}/"])
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.exception("[UbisoftPrefixManager] rsync spawn failed")
            return False
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=30 * 60,
            )
        except TimeoutError:
            logger.exception(
                "[UbisoftPrefixManager] rsync timed out — killing",
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return False
        if proc.returncode != 0:
            logger.error(
                "[UbisoftPrefixManager] rsync failed (%d): %s",
                proc.returncode,
                stderr.decode(errors="replace")[:300],
            )
            return False
        return True

    @staticmethod
    def fix_pfx_symlink(prefix_dir: str) -> None:
        """Fix pfx symlink."""
        pfx_link = os.path.join(prefix_dir, "pfx")
        if not os.path.islink(pfx_link):
            return
        try:
            current_target = os.readlink(pfx_link)
            if current_target in (prefix_dir, "."):
                return
            os.remove(pfx_link)
            os.symlink(prefix_dir, pfx_link)
            logger.info(
                "[UbisoftPrefixManager] fixed pfx symlink: %s → %s",
                current_target,
                prefix_dir,
            )
        except OSError as e:
            logger.warning(
                "[UbisoftPrefixManager] could not fix pfx symlink: %s",
                e,
            )

    def write_bootstrap_marker(
        self,
        prefix_dir: str,
        source: str,
        space_id: str | None,
    ) -> None:
        """Write bootstrap marker."""
        marker_path = os.path.join(
            prefix_dir,
            self._parent._config.bootstrap_marker,
        )
        # UTC keeps the marker comparable across machines and survives
        # DST transitions on the user's locale.
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        lines = [source, f"created={created_at}"]
        if space_id:
            lines.insert(1, f"game={space_id}")
            try:
                with open(
                    marker_path,
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write("\n".join(lines) + "\n")
            except OSError as e:
                logger.warning(
                    "[UbisoftPrefixManager] could not write bootstrap marker: %s",
                    e,
                )

    def try_inject_auth_state(
        self,
        prefix_paths: list[str],
    ) -> None:
        """Try inject auth state."""
        if not prefix_paths:
            return
        try:
            self._parent._inject_auth_state(prefix_paths)
        except Exception as e:
            logger.warning(
                "[UbisoftPrefixManager] auth state injection failed: %s",
                e,
            )
