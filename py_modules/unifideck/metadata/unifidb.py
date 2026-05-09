

from __future__ import annotations

import logging
import re
import string
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from ..utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from ..config import ConfigManager

logger = logging.getLogger(__name__)

UNIFIDB_CDN_BASE = ("https://cdn.jsdelivr.net/gh/mubaraknumann/unifiDB@main")
MATCH_THRESHOLD = 0.65

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
    normal
            break
    if not normalized:ized = normalize_title_for_matching(title)
    for article in ("the ", "a ", "an "):
        if normalized.startswith(article):
            normalized = normalized[len(article):]
        return "0_9"
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
    threshold: float = MATCH_THRESHOLD,
) -> dict[str, Any] | None:
    """Get best match."""
    if not candidates:
        return None
    scored: list[tuple[float, dict[str, Any]]] = []
    for c in candidates:
        name = c.get("title") or c.get("name") or ""
        score = score_title_match(search_title, name)
        if score >= threshold:
            scored.append((score, c))
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
    threshold = get_cfg(
        config, "metadata.unifidb.match_threshold", MATCH_THRESHOLD,
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
    best = get_best_match(title, games, threshold)
    if best:
        logger.debug("[unifidb] title match: %r", title)
        return game_to_cache_format(best)
    return None

async def _fetch_bucket(
    bucket: str, cdn_base: str, timeout: int,
) -> list[dict[str, Any]]:
    """Fetch bucket."""
    import aiohttp
    first_char = bucket[0] if bucket else "0_9"
    url = f"{cdn_base}/games/{first_char}/{bucket}.json"
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=timeout) as resp,
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
