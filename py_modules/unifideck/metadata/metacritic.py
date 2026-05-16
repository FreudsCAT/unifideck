

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

METACRITIC_COMPOSER_URL = (
    "https://backend.metacritic.com/composer/metacritic/pages/"
    "games-critic-reviews/{slug}/platform/pc/web"
)

DEFAULT_FETCH_TIMEOUT = 10
_EDITION_SUFFIXES = [
 r":?\s*Director's Cut",
 r":?\s*Game of the Year Edition",
 r":?\s*GOTY Edition",
 r":?\s*Remastered",
 r":?\s*Definitive Edition",
 r":?\s*Bonus Edition",
 r":?\s*Deluxe Edition",
 r":?\s*Special Edition",
 r":?\s*Anniversary Edition",
 r":?\s*Complete Edition",
 r":?\s*Ultimate Edition",
 r":?\s*Gold Edition",
 r":?\s*Enhanced Edition",
]

_ROMAN_MAP = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V"}
_ARABIC_MAP = {v: k for k, v in _ROMAN_MAP.items()}

def slugify_game_name(name: str) -> str:
    """Slugify game name."""
    name = (
    unicodedata.normalize("NFKD", name)
    .encode("ascii", "ignore")
    .decode("utf-8")
    .lower()
    )
    name = name.replace("+", "-plus-")
    name = re.sub(r"[^a-z0-9 \-]+", "", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")

def clean_title(title: str) -> str:
    """Clean title."""
    return re.sub(r"[\u2122\u00AE]", "", title).strip()

def strip_suffixes(title: str) -> str:
    """Strip suffixes."""
    cleaned = title
    for suffix in _EDITION_SUFFIXES:
        cleaned = re.sub(suffix, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def to_roman(num_str: str) -> str | None:
    """To roman."""
    return _ROMAN_MAP.get(num_str)

def to_arabic(roman_str: str) -> str | None:
    """To arabic."""
    return _ARABIC_MAP.get(roman_str)

def get_numeral_variants(title: str) -> list[str]:
    """Get numeral variants."""
    candidates: list[str] = []
    m = re.search(r"\b(I|II|III|IV|V)$", title)
    if m:
        arabic = to_arabic(m.group(1))
        if arabic:
            candidates.append(title[:m.start()] + arabic)
    m = re.search(r"\b([1-5])$", title)
    if m:
        roman = to_roman(m.group(1))
        if roman:
            candidates.append(title[:m.start()] + roman)
    def _arabic_to_roman(match: re.Match[str]) -> str:
        """Arabic to roman."""
        return to_roman(match.group(1)) or match.group(0)
    def _roman_to_arabic(match: re.Match[str]) -> str:
        """Roman to arabic."""
        return to_arabic(match.group(1)) or match.group(0)
    subbed = re.sub(r"\b([1-5])\b", _arabic_to_roman, title)
    if subbed != title:
        candidates.append(subbed)
    subbed = re.sub(r"\b(I|II|III|IV|V)\b", _roman_to_arabic, title)
    if subbed != title:
        candidates.append(subbed)
    return list(dict.fromkeys(candidates))

def sanitize_description(text: str, max_length: int = 1000) -> str:
    """Sanitize description."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        return text[: max_length - 1].rstrip() + "…"
    return text

@dataclass
class MetacriticScore:
    """Metacritic score."""
    title: str
    slug: str
    metascore: int | None
    user_score: float | None
    description: str
    url: str
    def to_dict(self) -> dict[str, Any]:
        """To dict."""
        return {
        "title": self.title,
        "slug": self.slug,
        "metascore": self.metascore,
        "user_score": self.user_score,
        "description": self.description,
        "url": self.url,
        }

async def fetch_score(
 title: str, config: ConfigManager | None = None) -> MetacriticScore | None:
    """Fetch score."""
    composer_url = get_cfg(
        config, "metadata.metacritic.composer_url",
        "https://backend.metacritic.com/composer/metacritic/"
        "pages/games/{slug}/web",
    )
    timeout = get_cfg(
        config, "metadata.metacritic.fetch_timeout_seconds", 15,
    )
    candidates = _slug_candidates(title)
    logger.debug("[metacritic] %d slug candidates for %r",
    len(candidates), title)
    for slug in candidates:
        data = await _fetch_composer(slug, composer_url, timeout)
        if data is None:
            continue
        score = _parse_composer_response(title, slug, data)
        if score is not None:
            return score
    return None

def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:
    """Cfg."""
    return get_cfg(config, key, default)

def _slug_candidates(title: str) -> list[str]:
    """Slug candidates."""
    variants = {title}
    cleaned = clean_title(title)
    variants.add(cleaned)
    variants.add(strip_suffixes(cleaned))
    for v in list(variants):
        for alt in get_numeral_variants(v):
            variants.add(alt)
    return [slugify_game_name(v) for v in variants if v.strip()]

async def _fetch_composer(slug: str, url_template: str, timeout: int) -> dict[str, Any] | None:  # noqa: ASYNC109 — timeout is API value passed to underlying lib (urllib/aiohttp/subprocess), not an asyncio.timeout() wrapper
    """Fetch composer."""
    import aiohttp
    url = url_template.format(slug=slug)
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            if resp.status != 200:
                return None
            return cast("dict[Any, Any] | None", await resp.json())
    except Exception as e:
        logger.debug(
            "[metacritic] fetch(%s) failed: %s", slug, e,
        )
        return None

def _parse_composer_response(title: str, slug: str, data: dict[str, Any]) -> MetacriticScore | None:
    """Parse composer response."""
    try:
        components = data.get("components", [])
        game_info = next(
            (
                c for c in components
                if c.get("type") == "gameInfo"
            ),
            None,
        )
        if not game_info:
            return None
        payload = game_info.get("data", {}).get("item", {})
        if not payload:
            return None
        return MetacriticScore(
            title=title,
            slug=slug,
            metascore=payload.get(
                "criticScoreSummary", {},
            ).get("score"),
            user_score=payload.get(
                "userScoreSummary", {},
            ).get("score"),
            description=sanitize_description(
                payload.get("description", ""),
            ),
            url=f"https://www.metacritic.com/game/{slug}/",
        )
    except (AttributeError, TypeError, KeyError) as e:
        logger.debug("[metacritic] parse error: %s", e)
        return None
