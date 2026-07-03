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
import random
from typing import TYPE_CHECKING, Any

import aiohttp

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
_HTTP_OK = 200
_HTTP_TOO_MANY = 429
_DEFAULT_TIMEOUT = 15.0
_BATCH_DELAY_S = 0.25  # Polite delay between requests to avoid 429s.
_MAX_RETRIES = 3  # extra attempts after a 429 before giving up
_RETRY_BASE_S = 1.0  # exponential backoff base for 429 retries
_MAX_RETRY_AFTER_S = 30.0  # cap a server-supplied Retry-After


def _retry_after_seconds(response: aiohttp.ClientResponse) -> float | None:
    """Parse a numeric ``Retry-After`` header (seconds), clamped."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(float(raw), _MAX_RETRY_AFTER_S)
    except (TypeError, ValueError):
        return None


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

    Retries up to ``_MAX_RETRIES`` times, honoring a numeric ``Retry-After``
    header plus jitter (which de-syncs concurrent sync fetches). Returns the
    parsed inner ``data`` dict, or ``None`` on a non-OK status or transport
    error.
    """
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with sess.get(
                STEAM_APPDETAILS_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as response:
                if response.status == _HTTP_TOO_MANY and attempt < _MAX_RETRIES:
                    jitter = random.uniform(0, 0.5)  # noqa: S311
                    delay = (
                        _retry_after_seconds(response)
                        or _RETRY_BASE_S * (2**attempt)
                    ) + jitter
                    logger.debug(
                        "[steam.appdetails] %d rate-limited (429), "
                        "retry %d/%d in %.1fs",
                        steam_app_id,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                if response.status != _HTTP_OK:
                    return None
                payload = await response.json(content_type=None)
                return _parse_appdetails(payload, steam_app_id)
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.debug(
                "[steam.appdetails] %d failed: %s",
                steam_app_id,
                exc,
            )
            return None
    return None


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
    retries with exponential backoff, honoring a numeric ``Retry-After``
    header, up to ``_MAX_RETRIES`` times before returning ``None``.
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
