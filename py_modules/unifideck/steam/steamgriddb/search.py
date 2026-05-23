"""6-pass title→game_id search ladder.

Composes :mod:`match` primitives with the SGDB autocomplete API to
resolve a free-form game title to an SGDB game ID, with strict
franchise-confusion guards.

Pass strategy
=============

1. **Cleaned-query exact match** — send :func:`clean_search_query`
   output to ``/search/autocomplete/{q}``, then normalised exact match
   against the returned names.
2. **Edition-stripped match** — strip suffixes ("Deluxe Edition" etc.)
   from both query and candidates before comparing.
3. **Scored match @ 0.85** — Jaccard word-set overlap above the
   franchise-confusion threshold.
4. **Retry with stripped base** — re-query SGDB using the
   edition-stripped title (sometimes the SGDB entry is indexed
   without the suffix, so the autocomplete returned the wrong
   substring match).
5. **Publisher prefix strip** — for each known prefix ("ea sports",
   "tom clancys", …), if the title starts with it, retry without.
6. **Fuzzy fallback @ 0.50** — accept the best-scoring candidate from
   any pass if it clears the lower threshold. Logged at INFO so
   regressions in match quality are visible.

If all 6 passes fail, returns ``None`` and the caller falls through
to Steam CDN.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .constants import PUBLISHER_PREFIXES
from .match import (
    clean_search_query,
    normalize_for_match,
    score_match,
    strip_edition_suffix,
)

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)


async def _autocomplete(
    session: aiohttp.ClientSession,
    base: str,
    api_key: str,
    query: str,
    timeout_sec: int,
) -> list[dict[str, Any]]:
    """Single SGDB ``/search/autocomplete/{query}`` call.

    Returns the raw ``data`` list (each item has at least ``id`` +
    ``name``). Empty list on any failure — never raises.
    """
    import aiohttp

    url = f"{base}/search/autocomplete/{query}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            if resp.status != 200:
                logger.debug(
                    "[sgdb.search] autocomplete(%r) → HTTP %d",
                    query, resp.status,
                )
                return []
            payload = await resp.json()
    except (TimeoutError, aiohttp.ClientError, OSError, ValueError) as e:
        logger.debug(
            "[sgdb.search] autocomplete(%r) failed: %s", query, e,
        )
        return []
    if not payload.get("success"):
        return []
    data = payload.get("data") or []
    return data if isinstance(data, list) else []


def _best_exact_or_edition(
    results: list[dict[str, Any]],
    query_norm: str,
    query_base: str,
) -> int | None:
    """Passes 1 + 2 combined — exact match then edition-stripped.

    Splitting them across two loops would scan the result set twice
    for no benefit; the inner check is cheap.
    """
    for item in results:
        name = str(item.get("name", ""))
        item_norm = normalize_for_match(name)
        if item_norm == query_norm:
            logger.debug(
                "[sgdb.search] exact match: %r → id=%s",
                query_norm, item.get("id"),
            )
            return _to_id(item.get("id"))
    for item in results:
        name = str(item.get("name", ""))
        item_norm = normalize_for_match(name)
        item_base = strip_edition_suffix(item_norm)
        if item_base == query_base:
            logger.debug(
                "[sgdb.search] edition match: %r → id=%s",
                query_base, item.get("id"),
            )
            return _to_id(item.get("id"))
    return None


def _best_scored(
    results: list[dict[str, Any]],
    query_norm: str,
    query_base: str,
    threshold: float,
) -> tuple[float, int | None]:
    """Pass 3 / 6 — best Jaccard score across results.

    Returns ``(best_score, best_id_or_None)``. Caller compares against
    ``threshold`` to decide whether to accept.
    """
    best_score = 0.0
    best_id: int | None = None
    for item in results:
        name = str(item.get("name", ""))
        item_norm = normalize_for_match(name)
        item_base = strip_edition_suffix(item_norm)
        score = max(
            score_match(query_norm, item_norm),
            score_match(query_base, item_base),
        )
        if score > best_score:
            best_score = score
            best_id = _to_id(item.get("id"))
    if best_id is not None and best_score >= threshold:
        return best_score, best_id
    return best_score, None


def _to_id(raw: Any) -> int | None:
    """Coerce SGDB ``id`` (sometimes int, sometimes numeric string)."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _pass4_retry_base(
    session: aiohttp.ClientSession,
    base: str,
    api_key: str,
    query_norm: str,
    query_base: str,
    timeout_sec: int,
) -> tuple[int | None, list[dict[str, Any]]]:
    """Pass 4: re-query SGDB with the edition-stripped title.

    Returns ``(matched_id_or_None, retry_results)`` — the second value
    is forwarded to the fuzzy fallback in pass 6 so we don't waste the
    extra round-trip.
    """
    retry = await _autocomplete(
        session, base, api_key, query_base, timeout_sec,
    )
    found = _best_exact_or_edition(retry, query_norm, query_base)
    if found is not None:
        logger.debug(
            "[sgdb.search] retry base match: %r → id=%d",
            query_base, found,
        )
        return found, retry
    score, hit = _best_scored(retry, query_norm, query_base, 0.85)
    if hit is not None:
        logger.debug(
            "[sgdb.search] retry scored: %r → id=%d (score=%.2f)",
            query_base, hit, score,
        )
        return hit, retry
    return None, retry


async def _pass5_publisher_prefix(
    session: aiohttp.ClientSession,
    base: str,
    api_key: str,
    query_base: str,
    timeout_sec: int,
) -> int | None:
    """Pass 5: strip a known publisher prefix and re-query.

    Only one prefix is tried per call — they're mutually exclusive at
    the start of a title, and trying every prefix would waste API
    budget on cold matches.
    """
    for prefix in PUBLISHER_PREFIXES:
        if not query_base.startswith(prefix + " "):
            continue
        short = query_base[len(prefix):].strip()
        if not short:
            return None
        prefix_results = await _autocomplete(
            session, base, api_key, short, timeout_sec,
        )
        for item in prefix_results:
            name = str(item.get("name", ""))
            item_norm = normalize_for_match(name)
            item_base = strip_edition_suffix(item_norm)
            if item_base in (short, query_base):
                coerced = _to_id(item.get("id"))
                if coerced is not None:
                    logger.debug(
                        "[sgdb.search] prefix-strip match: %r → id=%d",
                        short, coerced,
                    )
                    return coerced
        return None
    return None


async def search_game_id(
    session: aiohttp.ClientSession,
    base: str,
    api_key: str,
    title: str,
    *,
    timeout_sec: int,
) -> int | None:
    """6-pass SGDB game-id resolution. Returns ``None`` on miss.

    Logs each pass at DEBUG so ``[sgdb.search]`` greps in the Decky
    log show the full match trail when debugging artwork misses.
    """
    if not title:
        return None
    cleaned = clean_search_query(title)
    if not cleaned:
        return None
    query_norm = normalize_for_match(title)
    query_base = strip_edition_suffix(query_norm)

    # Pass 1+2: cleaned-query autocomplete → exact + edition match
    results = await _autocomplete(session, base, api_key, cleaned, timeout_sec)
    found = _best_exact_or_edition(results, query_norm, query_base)
    if found is not None:
        return found

    # Pass 3: scored match @ 0.85
    score3, id3 = _best_scored(results, query_norm, query_base, 0.85)
    if id3 is not None:
        logger.debug(
            "[sgdb.search] scored match: %r → id=%d (score=%.2f)",
            title, id3, score3,
        )
        return id3

    # Pass 4: retry with edition-stripped query
    if query_base and query_base != cleaned.lower():
        hit, retry = await _pass4_retry_base(
            session, base, api_key, query_norm, query_base, timeout_sec,
        )
        if hit is not None:
            return hit
        if retry:
            results = retry  # Carry forward for fuzzy fallback

    # Pass 5: publisher-prefix strip
    prefix_hit = await _pass5_publisher_prefix(
        session, base, api_key, query_base, timeout_sec,
    )
    if prefix_hit is not None:
        return prefix_hit

    # Pass 6: fuzzy fallback @ 0.50 across the last result set we have
    score6, id6 = _best_scored(results, query_norm, query_base, 0.50)
    if id6 is not None:
        logger.info(
            "[sgdb.search] fuzzy match: %r → id=%d (score=%.2f)",
            title, id6, score6,
        )
        return id6
    logger.debug(
        "[sgdb.search] no match for %r (best score=%.2f)",
        title, score6,
    )
    return None
