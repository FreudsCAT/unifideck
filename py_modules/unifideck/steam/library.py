"""steam/library.py — Steam install discovery + Steam Store search."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from unifideck.utils.config_helpers import get_cfg
from unifideck.utils.title_match import titles_match

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
        # SteamOS's bundled cert store predates several Steam Store CDN
        # cert rotations (~2024) so default SSL verification fails inside
        # the Decky plugin process — the symptom looks like every
        # ``library.search_store`` call silently returning ``None``,
        # which in turn makes ``MetadataService.enrich`` cache a
        # ``_negative`` sentinel for every game. ArtworkService's parallel
        # ``steam_search_appid`` (artwork/store_metadata.py:66) already
        # works around this by passing ``ssl=False`` to its connector;
        # mirroring that here. Hostname + chain validation are off, same
        # trade-off as the GOG OAuth flow (see ``ssl_helpers``).
        connector = aiohttp.TCPConnector(ssl=False)
        async with (
            aiohttp.ClientSession(
                connector=connector, timeout=timeout,
            ) as session,
            session.get(STEAM_STORE_SEARCH_URL, params=params) as response,
        ):
            if response.status != _HTTP_OK:
                return None
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.debug("[steam.search_store] %s failed: %s", title, exc)
        return None

    items = data.get("items") if isinstance(data, dict) else None
    if not items:
        return None
    # Validate the title instead of blindly trusting Steam's top hit.
    # ``items[0]`` is frequently a sequel / soundtrack / unrelated game
    # ("Control" → "Steam Controller", "Hades" → "Hades II", "Figment" →
    # "Figment - Soundtrack"), which would feed WRONG metadata + compat
    # downstream. Scan for the first result that actually IS this game;
    # return None (no data) rather than guess wrong.
    item = None
    app_id = 0
    for candidate in items:
        try:
            cid = int(candidate.get("id", 0))
        except (TypeError, ValueError):
            continue
        if cid <= 0:
            continue
        if titles_match(title, str(candidate.get("name", ""))):
            item = candidate
            app_id = cid
            break
    if item is None:
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
