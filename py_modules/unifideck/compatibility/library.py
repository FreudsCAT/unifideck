from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from ..config import ConfigManager
    from ..core.cache_manager import CacheManager
from ..utils.config_helpers import get_cfg
logger = logging.getLogger(__name__)
PROTONDB_TIERS = ("platinum", "gold", "silver", "bronze", "borked")
DECK_CATEGORIES = {
 0: "unknown",
 1: "unsupported",
 2: "playable",
 3: "verified",
}
PROTONDB_URL = (
 "https://www.protondb.com/api/v1/reports/summaries/{appid}.json"
)
DECK_VERIFIED_URL = (
 "https://store.steampowered.com/saleaction/"
 "ajaxgetdeckappcompatibilityreport?nAppID={appid}"
)
DEFAULT_USER_AGENT = "Unifideck/1.0 (compat-library)"
CACHE_NAMESPACE = "compat"
@dataclass
class CompatRating:
    """Compat rating."""
    appid: int | None = None
    title: str = ""
    protondb_tier: str | None = None
    deck_status: str = "unknown"
    sources: list[str] = field(default_factory=list)
    error: str | None = None
    def to_dict(self) -> dict[str, Any]:
        """To dict."""
        return {
        "appid": self.appid,
        "title": self.title,
        "protondb_tier": self.protondb_tier,
        "deck_status": self.deck_status,
        "sources": list(self.sources),
        "error": self.error,
        }
def parse_protondb_response(payload: dict[str, Any]) -> str | None:
    """Parse protondb response."""
    if not isinstance(payload, dict):
        return None
    tier = payload.get("tier")
    if isinstance(tier, str) and tier in PROTONDB_TIERS:
        return tier
    return None
def parse_deck_verified_response(payload: dict[str, Any]) -> str:
    """Parse DECK verified response."""
    if not isinstance(payload, dict):
        return "unknown"
    results = payload.get("results")
    if not isinstance(results, dict):
        return "unknown"
    cat = results.get("resolved_category", 0)
    try:
        return DECK_CATEGORIES.get(int(cat), "unknown")
    except (TypeError, ValueError):
        return "unknown"

def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:

    """Cfg."""
    return get_cfg(config, key, default)
class CompatLibrary:
    """Compat library."""
    def __init__(
        self,
        cache: CacheManager | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        """Initialize the instance."""
        self._cache = cache
        self._config = config
        if cache is not None:
            ttl = int(get_cfg(config, "cache_ttl.compat", 604800))
            try:
                cache.register(CACHE_NAMESPACE, ttl_seconds=ttl)
            except Exception:
                pass
    async def get_for_appid(self, appid: int) -> CompatRating:
        """Get for appid."""
        cached = self._cache_get(str(appid))
        if cached is not None:
            return CompatRating(**cached)
        result = CompatRating(appid=appid)
        result.protondb_tier = await self._fetch_protondb(appid)
        if result.protondb_tier is not None:
            result.sources.append("protondb")
        result.deck_status = await self._fetch_deck_verified(appid)
        if result.deck_status != "unknown":
            result.sources.append("deck_verified")
        self._cache_set(str(appid), result.to_dict())
        return result
    async def get_for_title(self, title: str) -> CompatRating:
        """Get for title."""
        from ..steam.library import search_store
        steam = await search_store(title, config=self._config)
        if steam is None or "app_id" not in steam:
            return CompatRating(
                title=title, error="not_found_on_steam_store",
            )
        result = await self.get_for_appid(int(steam["app_id"]))
        result.title = title
        return result
    async def bulk_fetch(
    self, titles: list[str], delay_ms: int = 50,
    ) -> dict[str, CompatRating]:
        """Bulk fetch."""
        out: dict[str, CompatRating] = {}
        for title in titles:
            out[title] = await self.get_for_title(title)
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)
        return out
    async def _fetch_protondb(self, appid: int) -> str | None:
        """Fetch protondb."""
        import aiohttp
        url = PROTONDB_URL.format(appid=appid)
        timeout = int(_cfg(
        self._config, "compat.protondb_timeout_seconds", 30,
        ))
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    url,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    timeout=timeout,
                ) as resp,
            ):
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    return None
                return parse_protondb_response(
                    await resp.json(),
                )
        except Exception as e:
            logger.debug(
                "[compat] protondb(%d) failed: %s", appid, e,
            )
            return None

    async def _fetch_deck_verified(self, appid: int) -> str:

        """Fetch DECK verified."""
        import aiohttp
        url = DECK_VERIFIED_URL.format(appid=appid)
        timeout = int(_cfg(
        self._config, "compat.deck_verified_timeout_seconds", 10,
        ))
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    url,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    timeout=timeout,
                ) as resp,
            ):
                if resp.status != 200:
                    return "unknown"
                return parse_deck_verified_response(
                    await resp.json(),
                )
        except Exception as e:
            logger.debug(
                "[compat] deck(%d) failed: %s", appid, e,
            )
            return "unknown"
    def _cache_get(self, key: str) -> dict[str, Any] | None:
        """Cache get."""
        if self._cache is None:
            return None
        try:
            return self._cache.get(CACHE_NAMESPACE, key)
        except Exception:
            return None
    def _cache_set(
        self, key: str, value: dict[str, Any],
    ) -> None:
        """Cache set."""
        if self._cache is None:
            return
        try:
            self._cache.set(CACHE_NAMESPACE, key, value)
        except Exception:
            pass
def load_compat_cache():
    """Load compat cache."""
    logger.debug("[compat] load_compat_cache called via legacy path")
    return {}
def save_compat_cache(cache):
    """Save compat cache."""
    logger.debug("[compat] save_compat_cache called via legacy path")
    return True
async def search_steam_store(session=None, title="", **kwargs):
    """Search steam store."""
    from ..steam.library import search_store
    return await search_store(title)
async def fetch_protondb_rating(session=None, appid=0, **kwargs):
    """Fetch protondb rating."""
    lib = CompatLibrary()
    return await lib._fetch_protondb(int(appid))
async def fetch_deck_verified(session=None, appid=0, **kwargs):
    """Fetch DECK verified."""
    lib = CompatLibrary()
    return await lib._fetch_deck_verified(int(appid))
async def get_compat_for_title(session=None, title="", **kwargs):
    """Get compat for title."""
    lib = CompatLibrary()
    rating = await lib.get_for_title(title)
    status = "ok" if rating.error is None else rating.error
    return (status, rating.to_dict())
async def prefetch_compat(
    titles,
    _batch_size=10,
    delay_ms=50,
):
    """Prefetch compat."""
    lib = CompatLibrary()
    return await lib.bulk_fetch(list(titles), delay_ms=delay_ms)

class BackgroundCompatFetcher:

    """Background compat fetcher."""
    def __init__(self, *args, **kwargs):
        """Initialize the instance."""
        self._lib = CompatLibrary()
    def start(self):
        """Start."""
        pass
    def stop(self):
        """Stop."""
        pass
    async def fetch(self, title):
        """Fetch."""
        return await self._lib.get_for_title(title)