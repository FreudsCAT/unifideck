

from __future__ import annotations

import asyncio
import logging
import re
import string
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from unifideck.utils.config_helpers import get_cfg
from unifideck.utils.title_match import titles_match

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

UNIFIDB_CDN_BASE = ("https://cdn.jsdelivr.net/gh/mubaraknumann/unifiDB@main")
MATCH_THRESHOLD = 0.65

# Process-lifetime cache of fetched buckets.
#
# UnifiDB ships its catalog as static JSON files on jsdelivr's CDN —
# one file per ``<first-letter><second-letter-or-digit>`` bucket
# (about 36 buckets total, ``a.json``..``z.json`` + ``0_9.json`` etc.).
# A library of 1000+ games triggers ``lookup()`` once per game, but
# every game with the same prefix needs the SAME bucket — without
# memoisation the same file is downloaded ~30-60 times per sync.
#
# We memoise per (cdn_base, bucket) so a config change to point at a
# fork doesn't accidentally hit stale data. The cache is in-memory
# only: it dies with the plugin process, and the next sync re-fetches.
# That's the right TTL because:
#   1. UnifiDB updates are rare (manual PRs to the mubaraknumann/unifiDB
#      repo) — staleness within a single sync is impossible.
#   2. jsdelivr's edge cache has its own ~12h TTL so a fresh fetch is
#      cheap, no need to mirror it ourselves.
#   3. Eliminates the need for a CacheManager namespace + disk persistence
#      for what is essentially a request-coalescing optimisation.
#
# The lock prevents two concurrent ``lookup()`` calls for games in the
# same bucket from both issuing the underlying HTTP request — the
# second waits and reads from the cache. Important because
# MetadataService's new game-level concurrency (semaphore=5) means
# multiple ``lookup()`` calls can be in flight at once.
_bucket_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
_bucket_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _bucket_cache_clear() -> None:
    """Drop every memoised bucket. Test/dev helper only."""
    _bucket_cache.clear()
    _bucket_locks.clear()

def normalize_title_for_matching(title: str) -> str:
    """Normalize title for matching."""
    title = title.lower()
    title = re.sub(r"[\u2122\u00AE]", "", title)
    title = title.translate(
        str.maketrans("", "", string.punctuation),
    )
    title = re.sub(r"\s+", " ", title)
    return title.strip()

def get_first_char_for_bucket(title: str) -> str:
    """Get first char for bucket."""
    normalized = normalize_title_for_matching(title)
    if not normalized:
        return "0_9"
    for article in ("the ", "a ", "an "):
        if normalized.startswith(article):
            normalized = normalized[len(article):]
            break
    first = normalized[0]
    if not first.isalpha():
        return "0_9"
    second = (
        normalized[1]
        if len(normalized) > 1 and normalized[1].isalnum()
        else first
    )
    return f"{first}{second}"

def score_title_match(search: str, candidate: str) -> float:
    """Score title match."""
    a = normalize_title_for_matching(search)
    b = normalize_title_for_matching(candidate)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if a in b or b in a:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        if longer <= 2 * shorter:
            return 0.85
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersect = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return 0.8 * (len(intersect) / len(union))

def extract_store_id(game: dict[str, Any], store: str) -> str | None:

    """Extract store ID."""
    external = game.get("external_ids") or {}
    if not isinstance(external, dict):
        return None
    val = external.get(store)
    return str(val) if val is not None else None

def get_best_match(
    search_title: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Best title-fallback match, gated by the shared ``titles_match``.

    Only reached when no candidate carries the store-native id (the
    exact path in :func:`lookup`). ``titles_match`` decides accept/reject
    — rejecting the sequels the local substring scorer wrongly accepted
    at 0.85 ("Hades" → "Hades II", "Quake" → "Quake II", "Spelunky" →
    "Spelunky 2") while accepting publisher-prefix / Roman-numeral /
    edition variants its token-Jaccard missed ("Assassin's Creed II" ↔
    "Assassin's Creed 2"). ``score_title_match`` only ranks the
    survivors when a bucket holds several genuine variants.
    """
    scored: list[tuple[float, dict[str, Any]]] = []
    for c in candidates:
        name = c.get("title") or c.get("name") or ""
        if titles_match(search_title, name):
            scored.append((score_title_match(search_title, name), c))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]

def game_to_cache_format(game: dict[str, Any]) -> dict[str, Any]:
    """Game to cache format."""
    return {
        "title": game.get("title") or game.get("name") or "",
        "description": game.get("description", ""),
        "release_date": game.get("release_date", ""),
        "publisher": game.get("publisher", ""),
        "developers": game.get("developers", []),
        "genres": game.get("genres", []),
        "platforms": game.get("platforms", []),
        "external_ids": game.get("external_ids", {}),
    }

@dataclass
class UnifiDBResult:
    """Unifi dbresult."""
    title: str
    description: str
    release_date: str
    publisher: str
    developers: list[str]
    genres: list[str]
    def to_dict(self) -> dict[str, Any]:
        """To dict."""
        return {
            "title": self.title,
            "description": self.description,
            "release_date": self.release_date,
            "publisher": self.publisher,
            "developers": self.developers,
            "genres": self.genres,
        }

async def lookup(
    store: str, game_id: str, title: str,
    config: ConfigManager | None = None,
) -> dict[str, Any] | None:

    """Lookup."""
    cdn_base = get_cfg(
        config, "metadata.unifidb.cdn_base", UNIFIDB_CDN_BASE,
    )
    timeout = get_cfg(
        config, "metadata.unifidb.fetch_timeout_seconds", 15,
    )
    bucket = get_first_char_for_bucket(title)
    games = await _fetch_bucket(bucket, cdn_base, timeout)
    if not games:
        return None
    for game in games:
        if extract_store_id(game, store) == game_id:
            logger.debug(
                "[unifidb] id match: %s:%s", store, game_id,
            )
            return game_to_cache_format(game)
    best = get_best_match(title, games)
    if best:
        logger.debug("[unifidb] title match: %r", title)
        return game_to_cache_format(best)
    return None

async def _fetch_bucket(
    bucket: str, cdn_base: str, timeout: int,  # noqa: ASYNC109 — timeout is API value passed to underlying lib (urllib/aiohttp/subprocess), not an asyncio.timeout() wrapper
) -> list[dict[str, Any]]:
    """Return the parsed bucket, memoised for the plugin's lifetime.

    First call for a (cdn_base, bucket) pair fetches over HTTPS;
    every subsequent call returns the cached list. See the module
    docstring on ``_bucket_cache`` for the rationale.

    The per-key ``asyncio.Lock`` collapses concurrent fetches for
    the same bucket into a single HTTP request — without it, a
    sync that processes games A1, A2, A3 in parallel would issue
    three identical ``a.json`` GETs before the first one finished.
    """
    key = (cdn_base, bucket)
    cached = _bucket_cache.get(key)
    if cached is not None:
        return cached
    lock = _bucket_locks.setdefault(key, asyncio.Lock())
    async with lock:
        # Re-check after acquiring the lock — another coroutine may
        # have populated the cache while we were waiting.
        cached = _bucket_cache.get(key)
        if cached is not None:
            return cached
        data = await _fetch_bucket_uncached(bucket, cdn_base, timeout)
        _bucket_cache[key] = data
        return data


async def _fetch_bucket_uncached(
    bucket: str, cdn_base: str, timeout: int,  # noqa: ASYNC109 — timeout is API value passed to underlying lib (urllib/aiohttp/subprocess), not an asyncio.timeout() wrapper
) -> list[dict[str, Any]]:
    """Single HTTP fetch of one bucket file. Caller owns memoisation."""
    import aiohttp
    first_char = bucket[0] if bucket else "0_9"
    url = f"{cdn_base}/games/{first_char}/{bucket}.json"
    try:
        # ssl=False — see library.search_store's comment. The UnifiDB
        # CDN is on jsdelivr which most clients verify fine, but
        # SteamOS's outdated cert store breaks even those handshakes
        # from inside the Decky plugin process; matching the
        # workaround the other metadata modules use.
        connector = aiohttp.TCPConnector(ssl=False)
        async with (
            aiohttp.ClientSession(connector=connector) as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            if resp.status != 200:
                return []
            data = await resp.json()
            if isinstance(data, list):
                return data
            return []
    except Exception as e:
        logger.debug(
            "[unifidb] fetch(%s) failed: %s", url, e,
        )
        return []
