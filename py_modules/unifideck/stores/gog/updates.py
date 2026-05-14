"""Background update checker for installed GOG games.

OP-50g | py_modules/unifideck/stores/gog/updates.py

``GOGUpdatesChecker`` periodically polls GOG.com for new versions of
installed games and emits events when an update is available. The
check is rate-limited to avoid abusing the GOG API; the cached
"last-checked" timestamp lives in the cache manager and is invalidated
when a new install is detected.

Update application itself is delegated to the installer pipeline
(``install/installer.py``, OP-51a) which re-runs gogdl in update mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from unifideck.core.types import Result

from .config import GOGConfig
from .http import fetch_json_get
from .tokens import GOGTokenManager

logger = logging.getLogger(__name__)
_CONTENT_SYSTEM_URL_TEMPLATE = (
    "https://content-system.gog.com/products/{game_id}/os/windows/builds?generation=2"
)
_UPDATE_CHECK_TIMEOUT_S = 10.0


class GOGUpdatesChecker:
    """Gogupdates checker."""

    def __init__(
        self,
        config: GOGConfig,
        tokens: GOGTokenManager,
        gogdl_bin: str,
        get_installed_ids: Callable[[], list[str]],
        resolve_install_info: Callable[
            [str],
            dict[str, str | None] | None,
        ],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._tokens = tokens
        self._gogdl_bin = gogdl_bin
        self._get_installed = get_installed_ids
        self._resolve_info = resolve_install_info

    @staticmethod
    def get_local_build_id(install_path: str, game_id: str) -> str | None:
        """Get local build ID."""
        install_p = Path(install_path)
        for search_dir in (install_p, install_p / "game"):
            info_file = search_dir / f"goggame-{game_id}.info"
            if not info_file.is_file():
                continue
            try:
                data = json.loads(
                    info_file.read_text(encoding="utf-8"),
                )
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(
                    "[GOGUpdatesChecker] info read failed for %s: %s",
                    info_file,
                    e,
                )
                continue
            build_id = data.get("buildId")
            if build_id:
                logger.debug(
                    "[GOGUpdatesChecker] local buildId for %s: %s",
                    game_id,
                    build_id,
                )
                return str(build_id)
        marker_path = install_p / ".unifideck-id"
        if marker_path.is_file():
            try:
                data = json.loads(
                    marker_path.read_text(
                        encoding="utf-8",
                    ).strip(),
                )
            except (OSError, json.JSONDecodeError):
                return None
            if isinstance(data, dict):
                build_id = data.get("buildId")
                if build_id:
                    logger.debug(
                        "[GOGUpdatesChecker] marker buildId for %s: %s",
                        game_id,
                        build_id,
                    )
                    return str(build_id)
        return None

    async def check_for_game_update(self, game_id: str) -> bool | None:
        """Check for game update."""
        if not await self._tokens.refresh_if_stale():
            logger.warning(
                "[GOGUpdatesChecker] not authenticated for update check of %s",
                game_id,
            )
            return None
        info = self._resolve_info(game_id)
        install_path = info.get("install_path") if info else None
        if not install_path:
            logger.debug(
                "[GOGUpdatesChecker] %s not installed, skipping",
                game_id,
            )
            return None
        local_build_id = self.get_local_build_id(
            install_path,
            game_id,
        )
        if not local_build_id:
            logger.warning(
                "[GOGUpdatesChecker] no local buildId for %s — cannot check for update",
                game_id,
            )
            return None
        remote_build_id = await self._fetch_remote_build_id(
            game_id,
        )
        if remote_build_id is None:
            return None
        logger.info(
            "[GOGUpdatesChecker] %s: local=%s, remote=%s",
            game_id,
            local_build_id,
            remote_build_id,
        )
        has_update = remote_build_id != local_build_id
        if has_update:
            logger.info(
                "[GOGUpdatesChecker] update available for %s",
                game_id,
            )
        return has_update

    async def _fetch_remote_build_id(self, game_id: str) -> str | None:
        """Fetch remote build ID."""
        access = self._tokens.access_token
        if not access:
            return None
        url = _CONTENT_SYSTEM_URL_TEMPLATE.format(
            game_id=game_id,
        )
        data = await fetch_json_get(
            url,
            bearer=access,
            user_agent=self._config.user_agent,
            timeout=_UPDATE_CHECK_TIMEOUT_S,
            log_prefix=f"[GOGUpdatesChecker] {game_id}",
        )
        if not isinstance(data, dict):
            return None
        items = data.get("items")
        if not isinstance(items, list) or not items:
            logger.warning(
                "[GOGUpdatesChecker] no builds returned for %s",
                game_id,
            )
            return None
        first = items[0]
        if not isinstance(first, dict):
            return None
        build_id = first.get("build_id")
        if build_id is None:
            return None
        return str(build_id)

    async def check_for_updates(self) -> list[str]:
        """Check for updates."""
        installed_ids = self._get_installed()
        if not installed_ids:
            return []
        updates: list[str] = []
        for game_id in installed_ids:
            has_update = await self.check_for_game_update(
                game_id,
            )
            if has_update:
                updates.append(game_id)
        logger.info(
            "[GOGUpdatesChecker] bulk check: %d/%d have updates",
            len(updates),
            len(installed_ids),
        )
        return updates

    async def update_game(
        self,
        game_id: str,
        install_path: str | None = None,
    ) -> Result:
        """Update game."""
        gogdl_exists = await asyncio.to_thread(
            Path(self._gogdl_bin).is_file,
        )
        if not gogdl_exists:
            return Result(
                success=False,
                error="gogdl_not_found",
            )
        resolved_path, path_failure = self._update_resolve_path(
            game_id,
            install_path,
        )
        if path_failure is not None:
            return cast("Result", path_failure)
        if not await self._tokens.refresh_if_stale():
            return Result(
                success=False,
                error="not_authenticated",
            )
        logger.info(
            "[GOGUpdatesChecker] starting update for %s at %s",
            game_id,
            resolved_path,
        )
        proc = await self._update_spawn_gogdl(
            game_id,
            resolved_path,
        )
        if proc is None:
            return Result(
                success=False,
                error="gogdl_spawn_failed",
            )
        await self._update_drain_output(proc)
        return await self._update_finalize(proc, game_id)

    def _update_resolve_path(self, game_id: str, install_path: str | None) -> tuple:
        """Update resolve path."""
        if install_path:
            return install_path, None
        info = self._resolve_info(game_id)
        if info and isinstance(info.get("install_path"), str):
            return info["install_path"], None
        return None, Result(
            success=False,
            error="install_path_not_found",
        )

    async def _update_spawn_gogdl(self, game_id: str, install_path: str) -> Any | None:
        """Update spawn GOGDL."""
        cmd = [
            self._gogdl_bin,
            "--auth-config-path",
            self._config.auth_config_path,
            "update",
            game_id,
            "--path",
            install_path,
            "--platform",
            "windows",
        ]
        try:
            env, cleanup = await self._tokens.acquire_gogdl_creds()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            proc._unifideck_gogdl_cleanup = cleanup
            return proc
        except OSError:
            logger.exception("[GOGUpdatesChecker] gogdl spawn failed")
            return None

    @staticmethod
    async def _update_drain_output(proc: Any) -> None:
        """Update drain output."""
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line_str = line.decode(errors="replace").strip()
            if line_str:
                logger.info(
                    "[GOGUpdatesChecker/update] %s",
                    line_str,
                )

    @staticmethod
    async def _update_finalize(proc: Any, game_id: str) -> Result:
        """Update finalize."""
        await proc.wait()
        if proc.returncode != 0:
            logger.error(
                "[GOGUpdatesChecker] update failed for %s (code %d)",
                game_id,
                proc.returncode,
            )
            return Result(
                success=False,
                error=f"update_failed_code_{proc.returncode}",
            )
        logger.info(
            "[GOGUpdatesChecker] successfully updated %s",
            game_id,
        )
        return Result(success=True)
