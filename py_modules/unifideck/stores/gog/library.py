"""GOG library facade — owned games + installed-state + display metadata.

OP-50c | py_modules/unifideck/stores/gog/library.py

``GOGLibrary`` is the public entry point of the library logic for the
GOG store. Responsibilities:

* fetch the owned-games list from GOG.com via the ``embed.gog.com``
  account endpoint (REST, JSON);
* scan ``download_dir`` for installed games (via ``.unifideck-id``
  markers);
* merge owned-list + install-state into uniform ``GameRecord`` entries
  ready for display in the UI;
* trigger marker migration (``library_migration.py``, OP-50d) on first
  run to upgrade pre-v6 markers to the canonical JSON format.

In-memory cached; invalidated on auth state change, install/uninstall,
or manual user refresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from unifideck.core.types import Game

from .config import GOGConfig
from .http import build_ssl_context, fetch_json_get
from .library_migration import _MarkerMigration
from .tokens import GOGTokenManager

logger = logging.getLogger(__name__)
_INSTALL_MARKER = ".unifideck-id"
_GOG_LIBRARY_TIMEOUT_S = 15.0


class GOGLibrary:
    """Goglibrary."""

    def __init__(
        self,
        config: GOGConfig,
        tokens: GOGTokenManager,
        exe_finder: Callable[[str], str | None] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._tokens = tokens
        self._find_exe = exe_finder
        self._migration = _MarkerMigration(self)

    def migrate_old_markers(self) -> dict[str, int]:
        """Migrate old markers."""
        return self._migration.migrate_old_markers()

    async def is_available(self) -> bool:
        """Check whether available."""
        if not self._tokens.has_tokens:
            loaded = await self._tokens.load()
            if not loaded:
                logger.info(
                    "[GOGLibrary] no tokens — not authenticated",
                )
                return False
        status = await self._probe_userdata()
        if status == 200:
            return True
        if status == 401:
            logger.warning(
                "[GOGLibrary] token expired (401), refreshing",
            )
            ok = await self._tokens.refresh_if_stale()
            if ok:
                status = await self._probe_userdata()
                return status == 200
            logger.warning(
                "[GOGLibrary] GOG token refresh failed - clearing dead credentials",
            )
            await self._tokens.clear()
            return False
        logger.warning(
            "[GOGLibrary] userdata probe returned %s",
            status,
        )
        return False

    async def _probe_userdata(self) -> int:
        """Probe userdata."""
        url = f"{self._config.base_url}/userData.json"
        access = self._tokens.access_token
        if not access:
            return 0
        if not url.startswith("https://"):
            logger.error(
                "[GOGLibrary] refusing non-https probe URL: %s",
                url,
            )
            return 0

        def _probe_sync() -> int:
            """Probe sync."""
            try:
                ctx = build_ssl_context()
                req = urllib.request.Request(
                    url,
                    headers={
                        "Authorization": f"Bearer {access}",
                        "User-Agent": self._config.user_agent,
                    },
                )
                with urllib.request.urlopen(
                    req,
                    timeout=5.0,
                    context=ctx,
                ) as response:
                    return cast("int", response.status)
            except urllib.request.HTTPError as e:
                return e.code
            except Exception as e:
                logger.debug(
                    "[GOGLibrary] probe error: %s",
                    e,
                )
                return 0

        return await asyncio.to_thread(_probe_sync)

    async def fetch_library(self) -> list[Game]:
        """Fetch library."""
        if not self._tokens.access_token:
            logger.warning("[GOGLibrary] not authenticated")
            return []
        games: list[Game] = []
        current_page = 1
        total_pages = 1
        base_url = self._config.base_url
        while current_page <= total_pages:
            url = (
                f"{base_url}/account/getFilteredProducts?"
                f"mediaType=1&page={current_page}"
            )
            data = await self._fetch_json(url)
            if data is None:
                logger.error(
                    "[GOGLibrary] page %d failed, stopping",
                    current_page,
                )
                break
            if current_page == 1:
                total_pages = int(
                    data.get("totalPages", 1) or 1,
                )
                total_results = int(
                    data.get("totalGamesFound", 0) or 0,
                )
                logger.info(
                    "[GOGLibrary] library has %d games across %d pages",
                    total_results,
                    total_pages,
                )
            for product in data.get("products", []):
                game_id = str(product.get("id", ""))
                if not game_id:
                    continue
                games.append(
                    Game(
                        app_id=0,
                        store="gog",
                        store_game_id=game_id,
                        title=product.get("title", "") or "",
                        installed=False,
                    )
                )
            current_page += 1
        logger.info(
            "[GOGLibrary] fetched %d games total",
            len(games),
        )
        return games

    async def get_game_slug(self, game_id: str) -> str | None:
        """Get game slug."""
        if not await self._tokens.refresh_if_stale():
            return None
        access = self._tokens.access_token
        if not access:
            return None
        url = f"{self._config.api_gog_url}/products/{game_id}?locale=en-US"
        data = await self._fetch_json(
            url,
            headers={
                "Authorization": f"Bearer {access}",
                "User-Agent": self._config.user_agent,
            },
        )
        if not isinstance(data, dict):
            return None
        slug = data.get("slug")
        if isinstance(slug, str) and slug:
            return slug
        links = data.get("links", {})
        if isinstance(links, dict):
            product_card = links.get("product_card", "")
            if isinstance(product_card, str) and "/game/" in product_card:
                return product_card.split("/game/")[-1].rstrip("/")
        return None

    def get_installed(self) -> list[str]:
        """Get installed."""
        download_path = Path(
            self._config.download_dir,
        ).expanduser()
        if not download_path.is_dir():
            return []
        installed: list[str] = []
        try:
            for entry in download_path.iterdir():
                if not entry.is_dir():
                    continue
                game_id = self._read_marker(str(entry))
                if game_id:
                    installed.append(game_id)
        except OSError:
            logger.exception("[GOGLibrary] get_installed scan failed")
            return []
        logger.info(
            "[GOGLibrary] found %d installed games",
            len(installed),
        )
        return installed

    def get_installed_game_info(self, game_id: str) -> dict[str, str | None] | None:
        """Get installed game info."""
        download_path = Path(
            self._config.download_dir,
        ).expanduser()
        if not download_path.is_dir():
            return None
        try:
            for entry in download_path.iterdir():
                if not entry.is_dir():
                    continue
                game_dir = str(entry)
                found = self._read_marker(game_dir)
                if found == game_id:
                    return {
                        "install_path": game_dir,
                        "executable": self._resolve_exe(game_dir),
                    }
                if found is None and self._has_goggame_info(
                    game_dir,
                    game_id,
                ):
                    logger.info(
                        "[GOGLibrary] found %s via goggame info fallback at %s",
                        game_id,
                        game_dir,
                    )
                    return {
                        "install_path": game_dir,
                        "executable": self._resolve_exe(game_dir),
                    }
        except OSError:
            logger.exception("[GOGLibrary] get_installed_game_info")
        return None

    @staticmethod
    def _read_marker(game_dir: str) -> str | None:
        """Read marker."""
        marker_path = Path(game_dir) / _INSTALL_MARKER
        if not marker_path.is_file():
            return None
        try:
            content = marker_path.read_text(
                encoding="utf-8",
            ).strip()
        except OSError as e:
            logger.warning(
                "[GOGLibrary] marker read failed: %s",
                e,
            )
            return None
        if not content:
            return None
        with contextlib.suppress(json.JSONDecodeError):
            data = json.loads(content)
            if isinstance(data, dict):
                return data.get("game_id") or data.get("gameId")
            if isinstance(data, (str, int)):
                return str(data)
        return content

    @staticmethod
    def _has_goggame_info(game_dir: str, game_id: str) -> bool:
        """Has goggame info."""
        for candidate in (
            game_dir,
            str(Path(game_dir) / "game"),
        ):
            try:
                if not Path(candidate).is_dir():
                    continue
                target = f"goggame-{game_id}.info"
                if (Path(candidate) / target).is_file():
                    return True
            except OSError:
                continue
        return False

    def _resolve_exe(self, install_path: str) -> str | None:
        """Resolve exe."""
        if self._find_exe is None:
            return None
        try:
            return self._find_exe(install_path)
        except Exception as e:
            logger.warning(
                "[GOGLibrary] exe resolution failed: %s",
                e,
            )
            return None

    async def _fetch_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> Any | None:
        """Fetch JSON."""
        return await fetch_json_get(
            url,
            bearer=self._tokens.access_token,
            user_agent=self._config.user_agent,
            timeout=_GOG_LIBRARY_TIMEOUT_S,
            extra_headers=headers,
            log_prefix="[GOGLibrary]",
        )
