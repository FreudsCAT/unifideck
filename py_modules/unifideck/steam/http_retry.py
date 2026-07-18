"""steam/http_retry.py — shared 429-aware GET helper for Steam Store endpoints.

Generalizes the duplicated retry loops that lived in
``steam/library.py`` (storesearch) and ``steam/appdetails.py``
(appdetails), and adds the previously missing 429 handling to
``appreviews`` and the Deck-Verified report fetch.

Two cooperating pieces:

* :func:`get_json_with_backoff` — GET with exponential backoff +
  jitter on HTTP 429, honouring a numeric ``Retry-After`` header
  (clamped to ``MAX_RETRY_AFTER_S``).
* :class:`RateLimitGate` — a shared "not before" latch. Every
  request ``wait()``s on it before sending; one 429 ``trip()``s it,
  pausing ALL in-flight workers for the Retry-After window instead
  of each worker independently burning its retry budget. Sustained
  throughput self-tunes to Steam's actual per-IP allowance, so the
  concurrency cap can sit above the polite floor without risking a
  retry storm.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

HTTP_OK = 200
HTTP_TOO_MANY = 429
MAX_RETRIES = 3  # extra attempts after a 429 before giving up
RETRY_BASE_S = 1.0  # exponential backoff base for 429 retries
MAX_RETRY_AFTER_S = 30.0  # cap a server-supplied Retry-After


class RateLimitGate:
    """Shared "not before" deadline pausing all workers after one 429."""

    def __init__(self) -> None:
        """Start with the gate open (deadline in the past)."""
        self._not_before = 0.0

    def trip(self, delay_s: float) -> None:
        """Push the shared deadline ``delay_s`` into the future.

        ``max`` so overlapping trips extend, never shorten, the pause.
        """
        self._not_before = max(self._not_before, time.monotonic() + delay_s)

    async def wait(self) -> None:
        """Sleep until the shared deadline passes (no-op when open)."""
        while True:
            remaining = self._not_before - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(remaining)


# One gate per process for ``store.steampowered.com`` — every Steam
# Store fetch (storesearch / appdetails / appreviews / deck-verified)
# funnels through it so a single 429 pauses them all together.
STEAM_STORE_GATE = RateLimitGate()


def retry_after_seconds(response: aiohttp.ClientResponse) -> float | None:
    """Parse a numeric ``Retry-After`` header (seconds), clamped."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(float(raw), MAX_RETRY_AFTER_S)
    except (TypeError, ValueError):
        return None


async def _json_or_none(response: aiohttp.ClientResponse) -> Any | None:
    """Decode the JSON body, or ``None`` for any non-200 status."""
    if response.status != HTTP_OK:
        return None
    return await response.json(content_type=None)


def _rate_limit_delay(
    response: aiohttp.ClientResponse, attempt: int, max_retries: int,
) -> float | None:
    """Backoff delay when ``response`` is a retryable 429, else ``None``.

    ``Retry-After`` (clamped) or exponential backoff, plus jitter —
    the jitter de-syncs concurrent sync fetches.
    """
    if response.status != HTTP_TOO_MANY or attempt >= max_retries:
        return None
    jitter = random.uniform(0, 0.5)  # noqa: S311
    return (
        retry_after_seconds(response) or RETRY_BASE_S * (2**attempt)
    ) + jitter


async def get_json_with_backoff(
    sess: aiohttp.ClientSession,
    url: str,
    *,
    timeout_s: float,
    log_tag: str,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    gate: RateLimitGate | None = None,
    max_retries: int = MAX_RETRIES,
) -> Any | None:
    """GET ``url`` on ``sess`` and return the decoded JSON payload.

    On HTTP 429: trips ``gate`` (pausing sibling workers), sleeps the
    ``Retry-After``/backoff window, and retries up to ``max_retries``
    times. Returns ``None`` on transport error, any other non-200
    status, or exhausted retries — callers keep their own payload
    parsing.
    """
    for attempt in range(max_retries + 1):
        if gate is not None:
            await gate.wait()
        try:
            async with sess.get(
                url,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as response:
                delay = _rate_limit_delay(response, attempt, max_retries)
                if delay is None:
                    return await _json_or_none(response)
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.debug("%s failed: %s", log_tag, exc)
            return None
        # 429 path — pause every sibling worker, then retry.
        if gate is not None:
            gate.trip(delay)
        logger.debug(
            "%s rate-limited (429), retry %d/%d in %.1fs",
            log_tag, attempt + 1, max_retries, delay,
        )
        await asyncio.sleep(delay)
    return None
