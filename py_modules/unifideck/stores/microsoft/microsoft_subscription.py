from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from unifideck.core.net import ssl_ctx_permissive as _ssl
from unifideck.core.types import SubscriptionTier

logger = logging.getLogger(__name__)
_PROBE_TIMEOUT_SECONDS = 10
_GSSV_CLIENT_HEADER = "XboxComBrowser"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)
_DEFAULT_OFFERING_ID = "xgpuweb"


@dataclass(frozen=True)
class SubscriptionProbeResult:
    """Subscription probe result.

    Includes the downstream artefacts the xCloud catalog reader needs:
    the short-lived ``gs_token`` (Bearer token for the regional core API)
    plus the list of streaming ``regions`` (to pick a baseUri).

    Note: ``programs`` from individual title responses is stale (lists
    every program a title was ever in). For tier-tagging in UI later,
    cross-reference each entitled productId with public sigls catalogs —
    that's a follow-up display feature, not a sync filter, since
    ``hasEntitlement`` already does the access-control job server-side.
    """

    tier: SubscriptionTier
    ok: bool
    error: str | None = None
    http_status: int | None = None
    gs_token: str | None = None
    regions: list[dict[str, Any]] = field(default_factory=list)
    market: str | None = None


async def probe_subscription(
    user_hash: str,
    gssv_xsts_token: str,
    endpoint_url: str,
    timeout_seconds: int = _PROBE_TIMEOUT_SECONDS,
    offering_id: str = _DEFAULT_OFFERING_ID,
) -> SubscriptionProbeResult:
    """Probe subscription."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: _probe_sync(
            user_hash, gssv_xsts_token, endpoint_url,
            timeout_seconds, offering_id,
        ),
    )


def _probe_sync(
    user_hash: str,
    gssv_xsts_token: str,
    endpoint_url: str,
    timeout_seconds: int,
    offering_id: str,
) -> SubscriptionProbeResult:
    """Probe sync.

    Azure App Gateway in front of xgpuweb requires a browser-shaped
    User-Agent. Without it we get a generic 403 HTML page before the
    request ever reaches the login service — historically we treated
    that as "no subscription" and poisoned the cache for the rest of
    the month. The login service itself requires the body
    ``{"offeringId": "...", "token": "<xsts>"}``.
    """
    headers = {
        "Authorization": f"XBL3.0 x={user_hash};{gssv_xsts_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "Cache-Control": "no-store, must-revalidate, no-cache",
        "x-gssv-client": _GSSV_CLIENT_HEADER,
        "User-Agent": _BROWSER_UA,
    }
    body = json.dumps({
        "offeringId": offering_id,
        "token": gssv_xsts_token,
    }).encode()
    req = urllib.request.Request(
        endpoint_url, data=body, headers=headers, method="POST",
    )
    http_result = _do_probe_http(req, timeout_seconds, endpoint_url)
    if isinstance(http_result, SubscriptionProbeResult):
        return http_result
    status, raw = http_result
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        logger.warning(
            "[SubscriptionProbe] response is not JSON: %s", e,
        )
        return SubscriptionProbeResult(
            tier=SubscriptionTier.NONE,
            ok=False,
            error="bad_response",
            http_status=status,
        )
    tier = _parse_tier_from_response(parsed)
    gs_token = parsed.get("gsToken") if isinstance(parsed, dict) else None
    market = parsed.get("market") if isinstance(parsed, dict) else None
    regions: list[dict[str, Any]] = []
    if isinstance(parsed, dict):
        offering = parsed.get("offeringSettings")
        if isinstance(offering, dict):
            r = offering.get("regions")
            if isinstance(r, list):
                regions = [x for x in r if isinstance(x, dict)]
    logger.info(
        "[SubscriptionProbe] %s responded %d, tier=%s, regions=%d, "
        "market=%s",
        endpoint_url, status, tier.value, len(regions), market,
    )
    return SubscriptionProbeResult(
        tier=tier,
        ok=True,
        http_status=status,
        gs_token=gs_token if isinstance(gs_token, str) else None,
        regions=regions,
        market=market if isinstance(market, str) else None,
    )


def _read_error_body(e: urllib.error.HTTPError) -> str:
    """Read first 200 chars of an HTTPError body, never raises."""
    try:
        return e.read().decode("utf-8", errors="replace")[:200]
    except Exception:
        return ""


def _http_error_label(status: int) -> str:
    """Map a transient HTTP status to a result.error label."""
    return {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
    }.get(status, "http_error")


def _do_probe_http(
    req: urllib.request.Request,
    timeout_seconds: int,
    endpoint_url: str,
) -> tuple[int, str] | SubscriptionProbeResult:
    """Do probe http."""
    try:
        with urllib.request.urlopen(
            req,
            timeout=timeout_seconds,
            context=_ssl(
                "Microsoft subscription — outdated Deck cert store",
            ),
        ) as resp:
            return resp.status, resp.read().decode(
                "utf-8", errors="replace",
            )
    except urllib.error.HTTPError as e:
        status = e.code
        # 401/403/404 from Azure App Gateway typically means the
        # request was rejected at the edge (missing User-Agent etc.) —
        # NOT "user has no subscription". Treat as transient so the
        # service-level cache doesn't poison NONE for end-of-month.
        if status in (401, 403, 404):
            logger.warning(
                "[SubscriptionProbe] HTTP %d on %s — transient, "
                "not caching as NONE (body=%s)",
                status, endpoint_url, _read_error_body(e),
            )
            return SubscriptionProbeResult(
                tier=SubscriptionTier.NONE,
                ok=False,
                error=_http_error_label(status),
                http_status=status,
            )
        logger.warning(
            "[SubscriptionProbe] unexpected HTTP %d on %s",
            status, endpoint_url,
        )
        return SubscriptionProbeResult(
            tier=SubscriptionTier.NONE,
            ok=False,
            error="http_error",
            http_status=status,
        )
    except (TimeoutError, urllib.error.URLError) as e:
        logger.warning(
            "[SubscriptionProbe] network error: %s", e,
        )
        return SubscriptionProbeResult(
            tier=SubscriptionTier.NONE,
            ok=False,
            error="timeout" if isinstance(e, TimeoutError) else "network",
        )
    except Exception as e:
        logger.warning(
            "[SubscriptionProbe] unexpected error: %s", e,
        )
        return SubscriptionProbeResult(
            tier=SubscriptionTier.NONE,
            ok=False,
            error="network",
        )


def _decode_jwt_payload(jwt: str) -> dict[str, Any] | None:
    """Decode the payload of a JWT without verifying the signature."""
    try:
        parts = jwt.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload).decode())
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _tier_from_gs_token(payload: dict[str, Any]) -> SubscriptionTier | None:
    """Authoritative tier signal: the ``offeringid`` JWT claim."""
    gs_token = payload.get("gsToken")
    if not isinstance(gs_token, str):
        return None
    claims = _decode_jwt_payload(gs_token) or {}
    candidate = claims.get("offeringid")
    if isinstance(candidate, str):
        return _match_tier_string(candidate)
    return None


def _tier_from_legacy_fields(
    payload: dict[str, Any], offering: dict[str, Any],
) -> SubscriptionTier | None:
    """Legacy fallback: top-level fields rarely populated by xgpuweb."""
    for key in ("subscriptionTier", "tier", "offeringId"):
        value = payload.get(key) or offering.get(key)
        if isinstance(value, str):
            parsed = _match_tier_string(value)
            if parsed is not None:
                return parsed
    return None


def _parse_tier_from_response(
    payload: dict[str, Any],
) -> SubscriptionTier:
    """Parse tier from the probe response.

    The login service response carries ``offeringSettings`` + ``gsToken``.
    The authoritative tier signal is the ``offeringid`` claim inside the
    ``gsToken`` JWT (e.g. ``XGPUWEB``).
    """
    if not isinstance(payload, dict):
        return SubscriptionTier.NONE  # type: ignore[unreachable]
    offering = payload.get("offeringSettings")
    if not isinstance(offering, dict):
        return SubscriptionTier.NONE
    regions = offering.get("regions")
    if not isinstance(regions, list) or len(regions) == 0:
        return SubscriptionTier.NONE
    tier = _tier_from_gs_token(payload)
    if tier is not None:
        return tier
    tier = _tier_from_legacy_fields(payload, offering)
    if tier is not None:
        return tier
    # Regions present but no recognisable tier — user IS entitled,
    # we just don't know which tier specifically.
    return SubscriptionTier.ACTIVE_UNKNOWN


def _match_tier_string(raw: str) -> SubscriptionTier | None:
    """Match tier string."""
    low = raw.lower()
    if "ultimate" in low or low == "xgpu" or low.startswith("xgpuweb"):
        if "f2p" in low:
            return SubscriptionTier.NONE
        return SubscriptionTier.ULTIMATE
    if "premium" in low:
        return SubscriptionTier.PREMIUM
    if "essential" in low or "core" in low:
        return SubscriptionTier.ESSENTIAL
    return None
