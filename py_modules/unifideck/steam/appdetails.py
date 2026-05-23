"""Rich Steam Store ``appdetails`` fetcher.

The public ``storesearch`` endpoint (used by ``search_store``)
returns only ``{app_id, name, header_image, price, release_date}``
— enough to match a title but not enough to populate Steam's
``GetAppDetails`` / ``GetAppOverviewByAppID`` shape that the
client-side store patcher needs.

This module hits the ``appdetails`` endpoint instead, which
returns the same payload Steam's own UI consumes for the Game
Info page: descriptions, screenshots, developers, publishers,
categories, genres, achievements, DLC, controller support,
platforms, supported languages.

Used by :class:`MetadataService` after a successful Steam
search, and persisted in the ``steam_appdetails`` cache so the
frontend can read it synchronously via the ``get_steam_metadata_cache``
RPC.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
_HTTP_OK = 200
_DEFAULT_TIMEOUT = 15.0
_BATCH_DELAY_S = 0.25  # Polite delay between requests to avoid 429s.


async def fetch_appdetails(
    steam_app_id: int,
    config: ConfigManager | None = None,
) -> dict[str, Any] | None:
    """Fetch the full Steam Store ``appdetails`` payload for ``steam_app_id``.

    Returns the inner ``data`` dict from the response shape
    ``{<id>: {success: bool, data: {...}}}``. Returns ``None`` on
    any network error or when the upstream marks ``success=False``
    (delisted / region-locked games).

    Args:
        steam_app_id: real Steam Store AppID.
        config: optional config manager (timeout override).

    Returns:
        Rich appdetails dict, or ``None`` on failure.
    """
    if steam_app_id <= 0:
        return None
    timeout_s = float(get_cfg(
        config, "network.steam_appdetails_timeout", _DEFAULT_TIMEOUT,
    ))
    params = {"appids": str(steam_app_id), "cc": "us", "l": "english"}
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(STEAM_APPDETAILS_URL, params=params) as response,
        ):
            if response.status != _HTTP_OK:
                return None
            payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.debug(
            "[steam.appdetails] %d failed: %s", steam_app_id, exc,
        )
        return None

    if not isinstance(payload, dict):
        return None
    entry = payload.get(str(steam_app_id))
    if not isinstance(entry, dict):
        return None
    if not entry.get("success"):
        return None
    data = entry.get("data")
    if not isinstance(data, dict):
        return None
    return data


async def fetch_appdetails_batch(
    steam_app_ids: list[int],
    config: ConfigManager | None = None,
    delay_s: float = _BATCH_DELAY_S,
) -> dict[int, dict[str, Any]]:
    """Fetch appdetails for many ids sequentially with a polite delay.

    Sequential (not gathered) so Steam doesn't rate-limit us.
    ``delay_s`` between calls. Ignores fetch failures — the
    returned dict only contains successful lookups.

    Args:
        steam_app_ids: list of real Steam AppIDs.
        config: optional config manager.
        delay_s: per-request throttle (default 0.25s).

    Returns:
        ``{steam_app_id: appdetails_dict}``. Failed lookups are
        omitted from the result.
    """
    out: dict[int, dict[str, Any]] = {}
    for app_id in steam_app_ids:
        data = await fetch_appdetails(app_id, config)
        if data is not None:
            out[app_id] = data
        if delay_s > 0:
            await asyncio.sleep(delay_s)
    return out
