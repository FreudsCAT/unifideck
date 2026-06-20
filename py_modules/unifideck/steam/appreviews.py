"""Steam Store ``appreviews`` summary fetcher.

The cached ``appdetails`` payload (see :mod:`.appdetails`) carries only
``recommendations.total`` — a raw review *count*, not the 0-9
``review_score`` / positive-percentage that Steam's library "Steam
Review" sort reads off ``AppOverview.review_score_with_bombs`` /
``review_percentage_with_bombs``.

This module hits the public ``appreviews`` summary endpoint to get
those, so the overview-enrichment layer can populate the review sort
for non-Steam shortcuts. Fetched once per real Steam AppID during the
metadata phase and cached in the ``steam_reviews`` namespace.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

STEAM_APPREVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"
_HTTP_OK = 200
_DEFAULT_TIMEOUT = 15.0


async def fetch_appreviews(
    steam_app_id: int,
    config: ConfigManager | None = None,
) -> dict[str, Any] | None:
    """Fetch the review summary for ``steam_app_id``.

    Returns ``{"review_score": int(0-9), "review_percentage": int(0-100),
    "total_reviews": int}`` or ``None`` on any error / no reviews.

    Args:
        steam_app_id: real Steam Store AppID.
        config: optional config manager (timeout override).
    """
    if steam_app_id <= 0:
        return None
    timeout_s = float(get_cfg(
        config, "network.steam_appdetails_timeout", _DEFAULT_TIMEOUT,
    ))
    url = STEAM_APPREVIEWS_URL.format(appid=steam_app_id)
    params = {
        "json": "1",
        "language": "all",
        "purchase_type": "all",
        "num_per_page": "0",  # summary only — we don't need review bodies
    }
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        # ``ssl=False`` for the same SteamOS cert reason as appdetails.
        connector = aiohttp.TCPConnector(ssl=False)
        async with (
            aiohttp.ClientSession(
                connector=connector, timeout=timeout,
            ) as session,
            session.get(url, params=params) as response,
        ):
            if response.status != _HTTP_OK:
                return None
            payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.debug("[steam.appreviews] %d failed: %s", steam_app_id, exc)
        return None

    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    summary = payload.get("query_summary")
    if not isinstance(summary, dict):
        return None
    try:
        review_score = int(summary.get("review_score", 0) or 0)
        total_positive = int(summary.get("total_positive", 0) or 0)
        total_reviews = int(summary.get("total_reviews", 0) or 0)
    except (TypeError, ValueError):
        return None
    if total_reviews <= 0:
        return None
    review_percentage = round(total_positive / total_reviews * 100)
    return {
        "review_score": review_score,
        "review_percentage": review_percentage,
        "total_reviews": total_reviews,
    }
