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
import contextlib
import datetime
import logging
import os
from pathlib import Path
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
            await asyncio.to_thread(lambda: Path(prefix_path).mkdir(parents=True, exist_ok=True))
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
            await asyncio.to_thread(lambda: Path(prefix_path).mkdir(parents=True, exist_ok=True))
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
            await asyncio.to_thread(lambda: Path(template_dir).mkdir(parents=True, exist_ok=True))
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

    async def create_template_from_auth_prefix(
        self,
        auth_dir: str,
    ) -> None:
        """Create template from auth prefix (canonical identity source).

        Under the shared-identity invariant the ``.template`` prefix is
        always an rsync clone of ``.upc-auth`` — never a standalone fresh
        install.  This guarantees all prefixes in the Ubisoft family share
        the same ``MachineGuid`` + DPAPI registry state, so the credential
        vault decrypts everywhere.
        """
        template_dir = self._parent._config.template_dir_expanded
        auth_real, template_real = await asyncio.to_thread(
            lambda: (os.path.realpath(auth_dir), os.path.realpath(template_dir)),
        )
        if auth_real == template_real:
            return
        logger.info(
            "[UbisoftPrefixManager] deriving template from auth prefix",
        )
        try:
            await asyncio.to_thread(lambda: Path(template_dir).mkdir(parents=True, exist_ok=True))
            ok = await self.rsync_clone(
                auth_dir,
                template_dir,
                exclude_games=True,
            )
            if not ok:
                logger.error(
                    "[UbisoftPrefixManager] rsync clone (auth→template) failed",
                )
                return
            self.write_bootstrap_marker(
                template_dir,
                "template_from_auth",
                None,
            )
            self.try_inject_auth_state([template_dir])
            logger.info(
                "[UbisoftPrefixManager] template derived from auth prefix "
                "— shared identity established",
            )
        except Exception as e:
            logger.warning(
                "[UbisoftPrefixManager] template derivation from auth failed: %s",
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
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
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
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
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
        pfx_link = str(Path(prefix_dir) / "pfx")
        if not Path(pfx_link).is_symlink():
            return
        try:
            current_target = Path(pfx_link).readlink()
            # ``readlink()`` returns ``Path`` but the comparison
            # set mixes ``Path`` (``prefix_dir``) and ``str``
            # (``"."``). Coerce both sides to ``str`` so mypy
            # sees overlapping types — and the semantic stays
            # identical (Path equality goes through ``__fspath__``
            # which compares the string form anyway).
            if str(current_target) in (str(prefix_dir), "."):
                return
            Path(pfx_link).unlink()
            Path(pfx_link).symlink_to(prefix_dir)
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
        marker_path = str(Path(prefix_dir) / self._parent._config.bootstrap_marker)
        # UTC keeps the marker comparable across machines and survives
        # DST transitions on the user's locale.
        created_at = datetime.datetime.now(datetime.UTC).isoformat()
        lines = [source, f"created={created_at}"]
        if space_id:
            lines.insert(1, f"game={space_id}")
        try:
            with Path(marker_path).open("w",
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
