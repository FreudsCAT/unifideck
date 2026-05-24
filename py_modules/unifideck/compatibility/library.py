"""Game compatibility ratings via ProtonDB and Steam Deck Verified."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
    from unifideck.core.cache_manager import CacheManager


logger = logging.getLogger(__name__)

PROTONDB_TIERS = ("platinum", "gold", "silver", "bronze", "borked")
DECK_CATEGORIES: dict[int, str] = {
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

# HTTP status code constants — kept here so PLR2004 doesn't flag the magic numbers in the fetch helpers below.
HTTP_OK = 200
HTTP_NOT_FOUND = 404

# Valve's Steam Deck verification report loc-tokens, mapped to the
# human-readable strings the Steam client shows next to each
# check/warning in its native compatibility modal. Ported from
# staging's ``DECK_TEST_RESULT_TOKENS`` (main.py:4488) so our
# panel's "Details" modal can render the same reasoning Steam does
# instead of an opaque "no detailed test results available".
DECK_TEST_RESULT_TOKENS: dict[str, str] = {
    "#SteamDeckVerified_TestResult_DefaultControllerConfigFullyFunctional":
        "All functionality is accessible when using the default controller "
        "configuration",
    "#SteamDeckVerified_TestResult_ControllerGlyphsMatchDeckDevice":
        "This game shows Steam Deck controller icons",
    "#SteamDeckVerified_TestResult_InterfaceTextIsLegible":
        "In-game interface text is legible on Steam Deck",
    "#SteamDeckVerified_TestResult_DefaultConfigurationIsPerformant":
        "This game's default graphics configuration performs well on Steam Deck",
    "#SteamDeckVerified_TestResult_LauncherInteractionIssues":
        "This game's launcher/setup tool may require the touchscreen or "
        "virtual keyboard, or have difficult to read text",
    "#SteamDeckVerified_TestResult_NativeResolutionNotDefault":
        "This game supports Steam Deck's native display resolution but does "
        "not set it by default and may require you to configure the display "
        "resolution manually",
    "#SteamDeckVerified_TestResult_ControllerGlyphsDoNotMatchDeckDevice":
        "This game sometimes shows non-Steam-Deck controller icons",
    "#SteamDeckVerified_TestResult_ExternalControllersNotSupportedLocalMultiplayer":
        "This game does not default to external Bluetooth/USB controllers "
        "on Deck, and may require manually switching the active controller "
        "via the Quick Access Menu",
    "#SteamOS_TestResult_GameStartupFunctional":
        "This game runs successfully on SteamOS",
}

# ``display_type`` value in a ``resolved_items`` entry that means
# "passed" (green checkmark). Anything else is treated as a warning.
_DECK_TEST_PASSED_DISPLAY_TYPE = 4


@dataclass
class CompatRating:
    """Compat rating."""

    appid: int | None = None
    title: str = ""
    protondb_tier: str | None = None
    deck_status: str = "unknown"
    deck_test_results: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    error: str | None = None
    def to_dict(self) -> dict[str, Any]:
        """To dict."""
        return {
        "appid": self.appid,
        "title": self.title,
        "protondb_tier": self.protondb_tier,
        "deck_status": self.deck_status,
        "deck_test_results": list(self.deck_test_results),
        "sources": list(self.sources),
        "error": self.error,
        }
def parse_protondb_response(payload: dict[str, Any]) -> str | None:
    """Parse protondb response."""
    if not isinstance(payload, dict):
        return None  # type: ignore[unreachable]  # fallback after path-type narrowing
    tier = payload.get("tier")
    if isinstance(tier, str) and tier in PROTONDB_TIERS:
        return tier
    return None
def parse_deck_verified_response(
    payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Parse the Steam Deck verification report.

    Returns ``(status, test_results)`` — ``status`` is one of
    ``"verified"``/``"playable"``/``"unsupported"``/``"unknown"``,
    ``test_results`` is a list of ``{text, passed}`` entries
    matching what Steam's native modal renders. Empty list when the
    upstream payload didn't include ``resolved_items`` (typical for
    non-Steam apps or games without a published verification).
    """
    if not isinstance(payload, dict):
        return "unknown", []  # type: ignore[unreachable]
    results = payload.get("results")
    if not isinstance(results, dict):
        return "unknown", []
    try:
        cat = int(results.get("resolved_category", 0))
    except (TypeError, ValueError):
        cat = 0
    status = DECK_CATEGORIES.get(cat, "unknown")
    items = results.get("resolved_items")
    test_results: list[dict[str, Any]] = []
    if isinstance(items, list):
        for entry in items:
            if not isinstance(entry, dict):
                continue
            token = str(entry.get("loc_token", ""))
            text = DECK_TEST_RESULT_TOKENS.get(token)
            if not text:
                continue
            passed = (
                entry.get("display_type") == _DECK_TEST_PASSED_DISPLAY_TYPE
            )
            test_results.append({"text": text, "passed": passed})
    return status, test_results

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
            except Exception as e:
                # Already registered or cache backend misconfigured;
                # lookups will still work, just without our preferred TTL.
                logger.debug("[CompatLibrary] cache.register failed: %s", e)
    async def get_for_appid(self, appid: int) -> CompatRating:
        """Get for appid."""
        cached = self._cache_get(str(appid))
        if cached is not None:
            result = CompatRating(**cached)
            # Self-healing upgrade from entries cached before
            # ``deck_test_results`` was added to ``to_dict``: when
            # the entry has a known verification status but no
            # test-result entries, re-fetch only the deck-verified
            # side and merge the results. ProtonDB is left alone
            # (it was already populated correctly in the old
            # format).
            if (
                result.deck_status != "unknown"
                and not result.deck_test_results
            ):
                status, test_results = await self._fetch_deck_verified(appid)
                result.deck_status = status
                result.deck_test_results = test_results
                self._cache_set(str(appid), result.to_dict())
            return result
        result = CompatRating(appid=appid)
        result.protondb_tier = await self._fetch_protondb(appid)
        if result.protondb_tier is not None:
            result.sources.append("protondb")
        status, test_results = await self._fetch_deck_verified(appid)
        result.deck_status = status
        result.deck_test_results = test_results
        if status != "unknown":
            result.sources.append("deck_verified")
        self._cache_set(str(appid), result.to_dict())
        return result
    async def get_for_title(
        self, title: str, shortcut_app_id: int | None = None,
    ) -> CompatRating:
        """Resolve ``title`` to a Steam AppID, then look up ProtonDB + Deck-Verified.

        When ``shortcut_app_id`` is provided we first try the
        ``steam_real_appid`` cache populated by
        :meth:`MetadataService.fetch_appdetails_for_game`. That
        cache holds the shortcut → real-Steam-AppID mapping for
        every non-Steam game the prior metadata phase saw, and
        skipping the live ``search_store`` call eliminates the
        per-game storesearch hit that used to trip Steam's rate
        limit (three services calling storesearch in parallel for
        every game across a 1000-title library).

        Falls back to ``search_store(title)`` on cache miss so the
        method still works for callers that don't have a shortcut
        AppID (e.g. ad-hoc lookups outside the sync pipeline).
        """
        steam_id: int | None = None
        if shortcut_app_id is not None:
            steam_id = self._lookup_cached_steam_id(shortcut_app_id)
        if steam_id is None:
            from unifideck.steam.library import search_store
            steam = await search_store(title, config=self._config)
            if steam is None or "app_id" not in steam:
                return CompatRating(
                    title=title, error="not_found_on_steam_store",
                )
            try:
                steam_id = int(steam["app_id"])
            except (TypeError, ValueError):
                return CompatRating(
                    title=title, error="not_found_on_steam_store",
                )
        result = await self.get_for_appid(steam_id)
        result.title = title
        return result

    def _lookup_cached_steam_id(self, shortcut_app_id: int) -> int | None:
        """Read the shortcut → real-Steam-AppID mapping written by MetadataService.

        Mirrors :meth:`ArtworkService._lookup_cached_steam_id`. Reads
        the ``steam_real_appid`` cache namespace's raw ``_data`` dict;
        the key is ``str(game.app_id)`` (signed 32-bit, matching how
        the sync layer stores AppIDs). Tries both signed and unsigned
        forms because Steam's frontend hands the unsigned form down
        through some code paths.
        """
        cache = getattr(self, "_cache", None)
        if cache is None:
            return None
        try:
            stores = getattr(cache, "_stores", None)
            if not isinstance(stores, dict):
                return None
            data = getattr(stores.get("steam_real_appid"), "_data", None)
            if not isinstance(data, dict):
                return None
            for key in self._appid_key_candidates(shortcut_app_id):
                value = data.get(key)
                if isinstance(value, int) and value > 0:
                    return value
        except Exception:
            return None
        return None

    @staticmethod
    def _appid_key_candidates(app_id: int) -> list[str]:
        """Return both signed and unsigned 32-bit string forms of an AppID."""
        forms: list[str] = [str(app_id)]
        if app_id > 0x7FFFFFFF:
            forms.append(str(app_id - 0x100000000))
        elif app_id < 0:
            forms.append(str(app_id + 0x100000000))
        return forms
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
            # ssl=False — SteamOS's outdated cert store breaks SSL
            # verification for several third-party hosts inside the
            # Decky plugin process. See library.search_store for the
            # same workaround.
            connector = aiohttp.TCPConnector(ssl=False)
            async with (
                aiohttp.ClientSession(connector=connector) as session,
                session.get(
                    url,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp,
            ):
                if resp.status == HTTP_NOT_FOUND:
                    return None
                if resp.status != HTTP_OK:
                    return None
                return parse_protondb_response(
                    await resp.json(),
                )
        except Exception as e:
            logger.debug(
                "[compat] protondb(%d) failed: %s", appid, e,
            )
            return None

    async def _fetch_deck_verified(
        self, appid: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Fetch Steam Deck verification status + per-test reasoning.

        Returns ``(status, test_results)``; mirrors the shape of
        :func:`parse_deck_verified_response`. Failures degrade to
        ``("unknown", [])`` so callers never have to handle
        exceptions.
        """
        import aiohttp
        url = DECK_VERIFIED_URL.format(appid=appid)
        timeout = int(_cfg(
        self._config, "compat.deck_verified_timeout_seconds", 10,
        ))
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with (
                aiohttp.ClientSession(connector=connector) as session,
                session.get(
                    url,
                    headers={"User-Agent": DEFAULT_USER_AGENT},
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp,
            ):
                if resp.status != HTTP_OK:
                    return "unknown", []
                return parse_deck_verified_response(
                    await resp.json(),
                )
        except Exception as e:
            logger.debug(
                "[compat] deck(%d) failed: %s", appid, e,
            )
            return "unknown", []
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
        except Exception as e:
            # Cache write failures are non-fatal: the rating was
            # computed successfully, we just won't re-use it.
            logger.debug("[CompatLibrary] cache.set %r failed: %s", key, e)
def load_compat_cache() -> dict[str, Any]:
    """Load compat cache (legacy passthrough — returns empty dict)."""
    logger.debug("[compat] load_compat_cache called via legacy path")
    return {}


def save_compat_cache(cache: dict[str, Any]) -> bool:
    """Save compat cache (legacy passthrough — always succeeds)."""
    logger.debug("[compat] save_compat_cache called via legacy path")
    return True


async def search_steam_store(
    session: Any | None = None,
    title: str = "",
    **kwargs: Any,
) -> dict[str, Any] | None:
    """Search Steam store for ``title`` (legacy passthrough)."""
    from unifideck.steam.library import search_store
    return await search_store(title)


async def fetch_protondb_rating(
    session: Any | None = None,
    appid: int = 0,
    **kwargs: Any,
) -> str | None:
    """Fetch the ProtonDB rating for ``appid`` (legacy passthrough)."""
    lib = CompatLibrary()
    return await lib._fetch_protondb(int(appid))


async def fetch_deck_verified(
    session: Any | None = None,
    appid: int = 0,
    **kwargs: Any,
) -> str:
    """Fetch the Steam Deck verification status for ``appid``.

    Module-level facade — keeps the legacy single-string return
    shape for older callers. New code should use
    :meth:`CompatLibrary._fetch_deck_verified` directly to also
    receive the per-test result entries.
    """
    lib = CompatLibrary()
    status, _ = await lib._fetch_deck_verified(appid)
    return status


async def get_compat_for_title(
    session: Any | None = None,
    title: str = "",
    **kwargs: Any,
) -> tuple[str, dict[str, Any]]:
    """Get compat rating for ``title`` (legacy passthrough)."""
    lib = CompatLibrary()
    rating = await lib.get_for_title(title)
    status = "ok" if rating.error is None else rating.error
    return (status, rating.to_dict())


async def prefetch_compat(
    titles: Iterable[str],
    _batch_size: int = 10,
    delay_ms: int = 50,
) -> Any:
    """Prefetch compat ratings for a list of ``titles`` (legacy)."""
    lib = CompatLibrary()
    return await lib.bulk_fetch(list(titles), delay_ms=delay_ms)


class BackgroundCompatFetcher:

    """Background compat fetcher."""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the instance."""
        self._lib = CompatLibrary()
    def start(self) -> None:
        """Start the background fetcher (legacy no-op)."""
    def stop(self) -> None:
        """Stop the background fetcher (legacy no-op)."""
    async def fetch(self, title: str) -> Any:
        """Fetch compat rating for ``title``."""
        return await self._lib.get_for_title(title)
