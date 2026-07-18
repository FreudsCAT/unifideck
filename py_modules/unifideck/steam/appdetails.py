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

from unifideck.steam.http_retry import STEAM_STORE_GATE, get_json_with_backoff
from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
_DEFAULT_TIMEOUT = 15.0
_BATCH_DELAY_S = 0.25  # Polite delay between requests to avoid 429s.


def _parse_appdetails(
    payload: Any,
    steam_app_id: int,
) -> dict[str, Any] | None:
    """Pull the inner ``data`` dict from the appdetails response shape."""
    if not isinstance(payload, dict):
        return None
    entry = payload.get(str(steam_app_id))
    if not isinstance(entry, dict) or not entry.get("success"):
        return None
    data = entry.get("data")
    return data if isinstance(data, dict) else None


async def _request_appdetails(
    sess: aiohttp.ClientSession,
    steam_app_id: int,
    params: dict[str, str],
    timeout_s: float,
) -> dict[str, Any] | None:
    """GET the appdetails payload on ``sess`` with HTTP 429 backoff.

    Retries via :func:`get_json_with_backoff` behind the shared
    ``STEAM_STORE_GATE``. Returns the parsed inner ``data`` dict, or
    ``None`` on a non-OK status or transport error.
    """
    payload = await get_json_with_backoff(
        sess,
        STEAM_APPDETAILS_URL,
        params=params,
        timeout_s=timeout_s,
        log_tag=f"[steam.appdetails] {steam_app_id}",
        gate=STEAM_STORE_GATE,
    )
    if payload is None:
        return None
    return _parse_appdetails(payload, steam_app_id)


async def fetch_appdetails(
    steam_app_id: int,
    config: ConfigManager | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any] | None:
    """Fetch the full Steam Store ``appdetails`` payload for ``steam_app_id``.

    Returns the inner ``data`` dict from the response shape
    ``{<id>: {success: bool, data: {...}}}``. Returns ``None`` on
    any network error or when the upstream marks ``success=False``
    (delisted / region-locked games).

    On **HTTP 429** (rate limited — common during a bulk sync) it
    retries with exponential backoff behind the shared
    ``STEAM_STORE_GATE``, honoring a numeric ``Retry-After`` header,
    before returning ``None``.
    """
    if steam_app_id <= 0:
        return None
    timeout_s = float(
        get_cfg(
            config,
            "network.steam_appdetails_timeout",
            _DEFAULT_TIMEOUT,
        )
    )
    params = {"appids": str(steam_app_id), "cc": "us", "l": "english"}
    if session is not None:
        return await _request_appdetails(
            session, steam_app_id, params, timeout_s,
        )
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session_new:
        return await _request_appdetails(
            session_new, steam_app_id, params, timeout_s,
        )


async def fetch_appdetails_batch(
    steam_app_ids: list[int],
    config: ConfigManager | None = None,
    delay_s: float = _BATCH_DELAY_S,
    session: aiohttp.ClientSession | None = None,
) -> dict[int, dict[str, Any]]:
    """Fetch appdetails for many ids sequentially with a polite delay.

    Sequential (not gathered) so Steam doesn't rate-limit us.
    ``delay_s`` between calls. Ignores fetch failures — the
    returned dict only contains successful lookups.
    """
    out: dict[int, dict[str, Any]] = {}
    for app_id in steam_app_ids:
        data = await fetch_appdetails(app_id, config, session)
        if data is not None:
            out[app_id] = data
        if delay_s > 0:
            await asyncio.sleep(delay_s)
    return out
