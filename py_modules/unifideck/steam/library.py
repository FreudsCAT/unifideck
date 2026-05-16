"""steam/library.py — Steam install discovery + Steam Store search."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

STEAM_PATH_CANDIDATES = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.steam/steam",
)
STEAM_STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch"

_HTTP_OK = 200
_DEFAULT_TIMEOUT = 10.0
_RESERVED_USERDATA_DIRS = frozenset({"0", "anonymous", "ac"})


def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:
    """Cfg."""
    return get_cfg(config, key, default)


def find_steam_path(config: ConfigManager | None = None) -> str | None:
    """Find steam path.

    Honours an optional ``paths.steam_root`` config override; falls back
    to the standard candidate locations. Returns the directory string
    on success, or ``None`` when no Steam install is detectable.
    """
    if config is not None:
        override = _cfg(config, "paths.steam_root", None)
        if override:
            full = str(Path(str(override)).expanduser())
            if (Path(full) / "steamapps").is_dir():
                return full
    for candidate in STEAM_PATH_CANDIDATES:
        full_path = str(Path(candidate).expanduser())
        if (Path(full_path) / "steamapps").is_dir():
            return full_path
    return None


def _find_most_recent_user(steam_path: str) -> str | None:
    """Find most recent user."""
    userdata = Path(steam_path) / "userdata"
    if not userdata.is_dir():
        return None
    latest: tuple[float, str] | None = None
    for entry in userdata.iterdir():
        if not entry.is_dir() or entry.name in _RESERVED_USERDATA_DIRS:
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest[0]:
            latest = (mtime, entry.name)
    return latest[1] if latest else None


def find_grid_path(
    steam_path: str | None = None,
    config: ConfigManager | None = None,
) -> str | None:
    """Find grid path."""
    base = steam_path or find_steam_path(config)
    if base is None:
        return None
    user = _find_most_recent_user(base)
    if user is None:
        return None
    return str(Path(base) / "userdata" / user / "config" / "grid")


def find_shortcuts_vdf(
    steam_path: str | None = None,
    config: ConfigManager | None = None,
) -> str | None:
    """Find shortcuts VDF."""
    base = steam_path or find_steam_path(config)
    if base is None:
        return None
    user = _find_most_recent_user(base)
    if user is None:
        return None
    return str(Path(base) / "userdata" / user / "config" / "shortcuts.vdf")


@dataclass
class SteamStoreResult:
    """Steam store result."""

    app_id: int
    name: str
    header_image: str
    price: str
    release_date: str

    def to_dict(self) -> dict[str, Any]:
        """To dict."""
        return asdict(self)


def _format_price(price_block: Any) -> str:
    """Format the Steam Store price block to a display string."""
    if not isinstance(price_block, dict):
        return ""
    final = price_block.get("final")
    if not isinstance(final, int):
        return ""
    if final == 0:
        return "Free"
    currency = price_block.get("currency", "")
    formatted = f"{final / 100:.2f}"
    return f"{formatted} {currency}".strip()


async def search_store(
    title: str,
    config: ConfigManager | None = None,
) -> dict[str, Any] | None:
    """Search store.

    Calls the Steam Store ``storesearch`` endpoint and returns the top
    match as a dict (``app_id``, ``name``, ``header_image``, ``price``,
    ``release_date``). Returns ``None`` on no hits or any network error.
    """
    if not title:
        return None
    timeout_s = float(_cfg(config, "network.steam_store_timeout", _DEFAULT_TIMEOUT))
    params = {"term": title, "l": "english", "cc": "us"}
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(STEAM_STORE_SEARCH_URL, params=params) as response:
                if response.status != _HTTP_OK:
                    return None
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("[steam.search_store] %s failed: %s", title, exc)
        return None

    items = data.get("items") if isinstance(data, dict) else None
    if not items:
        return None
    item = items[0]
    try:
        app_id = int(item.get("id", 0))
    except (TypeError, ValueError):
        return None
    if app_id <= 0:
        return None
    header_image = (
        f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"
    )
    return SteamStoreResult(
        app_id=app_id,
        name=str(item.get("name", "")),
        header_image=header_image,
        price=_format_price(item.get("price")),
        release_date=str(item.get("released", "")),
    ).to_dict()


async def batch_search_store(titles: list[str]) -> dict[str, dict[str, Any] | None]:
    """Batch search store."""
    if not titles:
        return {}
    results = await asyncio.gather(
        *(search_store(t) for t in titles),
        return_exceptions=False,
    )
    return dict(zip(titles, results, strict=False))
