from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from ..utils.config_helpers import get_cfg
if TYPE_CHECKING:
    from ..config import ConfigManager
logger = logging.getLogger(__name__)
STEAM_PATH_CANDIDATES = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.steam/steam",
)
STEAM_STORE_SEARCH_URL = (
    "https://store.steampowered.com/api/storesearch"
)
def find_steam_path(config: ConfigManager | None = None) -> str | None:
    """Find steam path."""
    candidates = get_cfg(
        config, "paths.steam_candidates", list(STEAM_PATH_CANDIDATES),
    )
    for candidate in candidates:
        expanded = Path(candidate).expanduser()
        if (expanded / "steamapps").is_dir():
            return str(expanded)
    return None
def find_grid_path(
    steam_path: str | None = None,
    config: ConfigManager | None = None,
) -> str | None:
    """Find grid path."""
    steam = steam_path or find_steam_path(config)
    if not steam:
        return None
    user_id = _find_most_recent_user(steam)
    if not user_id:
        return None
    grid_dir = (
        Path(steam) / "userdata" / user_id / "config" / "grid"
    )
    try:
        grid_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(
            "[steam/library] grid mkdir failed: %s", e,
        )
        return None
    return str(grid_dir)
def find_shortcuts_vdf(
    steam_path: str | None = None,
    config: ConfigManager | None = None,
) -> str | None:
    """Find shortcuts VDF."""
    steam = steam_path or find_steam_path(config)
    if not steam:
        return None
    user_id = _find_most_recent_user(steam)
    if not user_id:
        return None
    return str(
        Path(steam) / "userdata" / user_id
        / "config" / "shortcuts.vdf",
    )

def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:

    """Cfg."""
    return get_cfg(config, key, default)
def _find_most_recent_user(steam_path: str) -> str | None:
    """Find most recent user."""
    loginusers = (
        Path(steam_path) / "config" / "loginusers.vdf"
    )
    if not loginusers.is_file():
        return None
    try:
        text = loginusers.read_text(encoding="utf-8")
    except OSError:
        return None
    pattern = re.compile(
        r'"(\d{17})"\s*\{[^}]*"MostRecent"\s*"1"',
        re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(1) if m else None
@dataclass
class SteamStoreResult:
    """Steam store result."""
    app_id: int
    name: str
    header_image: str
    price: str
    release_date: str
    def to_dict(self) -> dict:
        """To dict."""
        return {
            "app_id": self.app_id,
            "name": self.name,
            "header_image": self.header_image,
            "price": self.price,
            "release_date": self.release_date,
        }
async def search_store(
    title: str, config: ConfigManager | None = None,
) -> dict | None:
    """Search store."""
    import aiohttp
    url = get_cfg(
        config, "metadata.steam_store.search_url",
        STEAM_STORE_SEARCH_URL,
    )
    timeout = get_cfg(
        config, "metadata.steam_store.search_timeout_seconds", 15,
    )
    params = {"term": title, "l": "english", "cc": "US"}
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url, params=params, timeout=timeout,
            ) as resp,
        ):
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception as e:
        logger.debug(
            "[steam/library] search(%s) failed: %s",
            title, e,
        )
        return None
    items = data.get("items") or []
    if not items:
        return None
    item = items[0]
    price = ""
    if isinstance(item.get("price"), dict):
        price = item["price"].get("final", "")
    return SteamStoreResult(
        app_id=int(item.get("id", 0)),
        name=item.get("name", ""),
        header_image=item.get("tiny_image", ""),
        price=str(price),
        release_date="",
    ).to_dict()

async def batch_search_store(titles: list[str]) -> dict:

    """Batch search store."""
    results = {}
    for title in titles:
        results[title] = await search_store(title)
    return results