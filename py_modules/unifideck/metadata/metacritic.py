"""metadata/metacritic.py — Metacritic score fetcher.

The Metacritic HTTP scraper wrapped in a clean async function
The scraper uses Metacritic's Next.js backend composer API
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

METACRITIC_COMPOSER_URL = (
    "https://backend.metacritic.com/composer/metacritic/pages/games/{slug}/web"
)


@dataclass(frozen=True)
class MetacriticScore:
    """Typed container for a Metacritic lookup result."""
    critic_score: int | None
    user_score: float | None
    url: str
    summary: str | None


def slugify(title: str) -> str:
    """Convert a game title to a URL-friendly slug for Metacritic."""
    # Remove special characters, convert to lowercase, replace spaces with hyphens
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug).strip('-')
    return slug


async def fetch_score(title: str, timeout: float = 10.0) -> MetacriticScore | None:
    """Fetch the Metacritic score for a game title."""
    slug = slugify(title)
    url = METACRITIC_COMPOSER_URL.format(slug=slug)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout) as response:
                if response.status == 404:
                    logger.debug("[Metacritic] No game found for slug: %s", slug)
                    return None
                
                if response.status != 200:
                    logger.warning("[Metacritic] API error %d for %s", response.status, title)
                    return None
                
                data = await response.json()
                return _parse_composer_response(data, slug)
                
    except asyncio.TimeoutError:
        logger.warning("[Metacritic] Timeout fetching score for %s", title)
    except Exception as e:
        logger.warning("[Metacritic] Failed to fetch score for %s: %s", title, e)
        
    return None


def _parse_composer_response(data: dict[str, Any], slug: str) -> MetacriticScore | None:
    """Extract a ``MetacriticScore`` from the composer JSON."""
    try:
        # The structure of Metacritic's composer API is nested
        # component -> props -> pageInfo/data
        components = data.get("components", [])
        
        critic_score = None
        user_score = None
        summary = None
        
        for comp in components:
            if comp.get("type") == "game-product-stats":
                stats = comp.get("props", {}).get("data", {}).get("items", [{}])[0]
                critic_score = stats.get("criticScore", {}).get("score")
                user_score = stats.get("userScore", {}).get("score")
            
            if comp.get("type") == "game-product-summary":
                summary = comp.get("props", {}).get("data", {}).get("summary")
        
        # Fallback to other parts of the JSON if components differ
        if critic_score is None:
            # Some versions have it in 'data'
            main_data = data.get("data", {}).get("item", {})
            critic_score = main_data.get("criticScoreSummary", {}).get("score")
            user_score = main_data.get("userScoreSummary", {}).get("score")
            summary = main_data.get("description")

        # Convert scores to proper types
        try:
            critic_score = int(critic_score) if critic_score is not None else None
        except (ValueError, TypeError):
            critic_score = None
            
        try:
            user_score = float(user_score) if user_score is not None else None
        except (ValueError, TypeError):
            user_score = None

        return MetacriticScore(
            critic_score=critic_score,
            user_score=user_score,
            url=f"https://www.metacritic.com/game/{slug}",
            summary=summary
        )
    except Exception as e:
        logger.debug("[Metacritic] Parse error for %s: %s", slug, e)
        return None