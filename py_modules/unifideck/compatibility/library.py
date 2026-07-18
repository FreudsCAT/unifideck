"""Game compatibility ratings via ProtonDB and Steam Deck Verified."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any

import aiohttp

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

# Marker keys (``dtr_checked``) live alongside the rating fields in
# the cached dict — filter to real dataclass fields so cached-entry
# construction can't crash on them.
_RATING_FIELDS = frozenset(f.name for f in fields(CompatRating))


def _rating_from_cached(cached: dict[str, Any]) -> CompatRating:
    """Build a ``CompatRating`` from a cached dict, ignoring marker keys."""
    return CompatRating(
        **{k: v for k, v in cached.items() if k in _RATING_FIELDS},
    )


def _stamped(result: CompatRating) -> dict[str, Any]:
    """``to_dict`` plus the ``dtr_checked`` one-shot self-heal marker."""
    payload = result.to_dict()
    payload["dtr_checked"] = True
    return payload


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
        *,
        deferred_writes: bool = False,
    ) -> None:
        """Initialize the instance.

        ``deferred_writes=True`` makes cache writes stay in memory
        until the owner flushes (CompatibilityService's per-sync
        loop writes once per game — eager persistence would rewrite
        the whole namespace file each time). Ad-hoc/legacy
        constructions keep the eager default.
        """
        self._cache = cache
        self._config = config
        self._deferred_writes = deferred_writes
        if cache is not None:
            ttl = int(get_cfg(config, "cache_ttl.compat", 604800))
            try:
                cache.register(CACHE_NAMESPACE, ttl_seconds=ttl)
            except Exception as e:
                # Already registered or cache backend misconfigured;
                # lookups will still work, just without our preferred TTL.
                logger.debug("[CompatLibrary] cache.register failed: %s", e)
    async def get_for_appid(
        self,
        appid: int,
        *,
        refresh: bool = False,
        session: aiohttp.ClientSession | None = None,
    ) -> CompatRating:
        """Look up ProtonDB + Deck-Verified for a real Steam AppID.

        ``refresh=True`` (force sync) skips the cache read and
        overwrites the entry with a fresh fetch — old data survives
        only if the caller never reaches the write (task cancelled).
        ``session`` lets the sync loop share one connection pool
        across all games.
        """
        cached = None if refresh else self._cache_get(str(appid))
        if cached is not None:
            result = _rating_from_cached(cached)
            # Self-healing upgrade from entries cached before
            # ``deck_test_results`` was added to ``to_dict``: when
            # the entry has a known verification status but no
            # test-result entries, re-fetch only the deck-verified
            # side and merge the results. ProtonDB is left alone
            # (it was already populated correctly in the old
            # format). The ``dtr_checked`` stamp makes this a
            # one-shot upgrade — games with genuinely no published
            # test results used to re-hit the endpoint every sync
            # forever.
            if (
                result.deck_status != "unknown"
                and not result.deck_test_results
                and not cached.get("dtr_checked")
            ):
                status, test_results = await self._fetch_deck_verified(
                    appid, session,
                )
                if status != "unknown":
                    # Only adopt a real answer — a transient fetch
                    # failure must not downgrade the cached status.
                    result.deck_status = status
                    result.deck_test_results = test_results
                self._cache_set(str(appid), _stamped(result))
            return result
        result = CompatRating(appid=appid)
        result.protondb_tier = await self._fetch_protondb(appid, session)
        if result.protondb_tier is not None:
            result.sources.append("protondb")
        status, test_results = await self._fetch_deck_verified(appid, session)
        result.deck_status = status
        result.deck_test_results = test_results
        if status != "unknown":
            result.sources.append("deck_verified")
        self._cache_set(str(appid), _stamped(result))
        return result
    async def get_for_title(
        self,
        title: str,
        shortcut_app_id: int | None = None,
        *,
        refresh: bool = False,
        session: aiohttp.ClientSession | None = None,
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
        AppID (e.g. ad-hoc lookups outside the sync pipeline). A
        failed search with a known shortcut is negative-cached
        (``steam_real_appid = -1``, MetadataService's convention)
        so the sync partition skips it instead of re-searching
        every sync; a force sync retries it via the metadata
        phase's re-resolution.

        ``refresh=True`` bypasses the compat cache read (force
        sync). The positive AppID mapping is still trusted — the
        metadata phase re-resolves it before this phase runs.
        """
        steam_id: int | None = None
        if shortcut_app_id is not None:
            steam_id = self._lookup_cached_steam_id(shortcut_app_id)
        if steam_id is None:
            from unifideck.steam.library import search_store
            steam = await search_store(
                title, config=self._config, session=session,
            )
            try:
                steam_id = int(steam["app_id"]) if steam else 0
            except (TypeError, ValueError, KeyError):
                steam_id = 0
            if steam_id <= 0:
                if shortcut_app_id is not None:
                    self._persist_steam_real_appid(shortcut_app_id, -1)
                return CompatRating(
                    title=title, error="not_found_on_steam_store",
                )
            # Backfill the shortcut → AppID mapping the metadata phase
            # missed, so the facet join surfaces this game's badge.
            if shortcut_app_id is not None:
                self._persist_steam_real_appid(shortcut_app_id, steam_id)
        result = await self.get_for_appid(
            steam_id, refresh=refresh, session=session,
        )
        result.title = title
        return result

    def cached_steam_mapping(self, shortcut_app_id: int) -> int | None:
        """Raw shortcut → Steam-AppID mapping, including negative sentinels.

        Mirrors :meth:`ArtworkService._lookup_cached_steam_id`. Reads
        the ``steam_real_appid`` cache namespace's raw ``_data`` dict;
        the key is ``str(game.app_id)`` (signed 32-bit, matching how
        the sync layer stores AppIDs). Tries both signed and unsigned
        forms because Steam's frontend hands the unsigned form down
        through some code paths. ``None`` = never resolved; ``<= 0``
        = negative-cached "no Steam counterpart".
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
                if isinstance(value, int):
                    return value
        except Exception:
            return None
        return None

    def _lookup_cached_steam_id(self, shortcut_app_id: int) -> int | None:
        """Positive-only view of :meth:`cached_steam_mapping`."""
        value = self.cached_steam_mapping(shortcut_app_id)
        return value if isinstance(value, int) and value > 0 else None

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
    async def _fetch_protondb(
        self,
        appid: int,
        session: aiohttp.ClientSession | None = None,
    ) -> str | None:
        """Fetch protondb.

        Reuses ``session`` when provided (the sync loop passes one
        shared session — creating a connector per game cost two TLS
        handshakes per title on a cold sync). No rate-limit gate:
        protondb.com is a different host from the Steam Store.
        """
        url = PROTONDB_URL.format(appid=appid)
        timeout = int(_cfg(
        self._config, "compat.protondb_timeout_seconds", 30,
        ))
        payload = await self._get_json(
            url, session, timeout, log_tag=f"[compat] protondb({appid})",
            gate=None,
        )
        if not isinstance(payload, dict):
            return None
        return parse_protondb_response(payload)

    async def _fetch_deck_verified(
        self,
        appid: int,
        session: aiohttp.ClientSession | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Fetch Steam Deck verification status + per-test reasoning.

        Returns ``(status, test_results)``; mirrors the shape of
        :func:`parse_deck_verified_response`. Failures degrade to
        ``("unknown", [])`` so callers never have to handle
        exceptions. Runs behind the shared ``STEAM_STORE_GATE``
        (same host as storesearch/appdetails).
        """
        from unifideck.steam.http_retry import STEAM_STORE_GATE
        url = DECK_VERIFIED_URL.format(appid=appid)
        timeout = int(_cfg(
        self._config, "compat.deck_verified_timeout_seconds", 10,
        ))
        payload = await self._get_json(
            url, session, timeout, log_tag=f"[compat] deck({appid})",
            gate=STEAM_STORE_GATE,
        )
        if not isinstance(payload, dict):
            return "unknown", []
        return parse_deck_verified_response(payload)

    async def _get_json(
        self,
        url: str,
        session: aiohttp.ClientSession | None,
        timeout_s: float,
        *,
        log_tag: str,
        gate: Any,
    ) -> Any | None:
        """GET JSON on ``session`` (or a one-shot session) with 429 backoff.

        ``ssl=False`` on the one-shot connector — SteamOS's outdated
        cert store breaks SSL verification for several third-party
        hosts inside the Decky plugin process. See
        ``library.search_store`` for the same workaround.
        """
        from unifideck.steam.http_retry import get_json_with_backoff
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        try:
            if session is not None:
                return await get_json_with_backoff(
                    session, url, timeout_s=timeout_s, log_tag=log_tag,
                    headers=headers, gate=gate,
                )
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as one_shot:
                return await get_json_with_backoff(
                    one_shot, url, timeout_s=timeout_s, log_tag=log_tag,
                    headers=headers, gate=gate,
                )
        except Exception as e:
            logger.debug("%s failed: %s", log_tag, e)
            return None
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
        """Cache set (deferred when ``deferred_writes`` — owner flushes)."""
        if self._cache is None:
            return
        try:
            if self._deferred_writes:
                self._cache.set(CACHE_NAMESPACE, key, value, flush=False)
            else:
                self._cache.set(CACHE_NAMESPACE, key, value)
        except Exception as e:
            # Cache write failures are non-fatal: the rating was
            # computed successfully, we just won't re-use it.
            logger.debug("[CompatLibrary] cache.set %r failed: %s", key, e)

    def _persist_steam_real_appid(
        self, shortcut_app_id: int, steam_id: int,
    ) -> None:
        """Backfill the shortcut → real-Steam-AppID mapping.

        Compat resolves the Steam AppID by title (``search_store`` +
        the edition-strip fallback) even for games the metadata phase
        negative-cached or never resolved — but only the metadata phase
        writes ``steam_real_appid``, so those games' already-fetched
        ProtonDB / Deck-Verified rating never linked back to the
        shortcut for the library-facets join (e.g. "Among Us": compat
        cached under 945360, but the shortcut had no mapping → no
        badge). Persist it here, keyed by the signed AppID to match how
        the sync layer writes it. Non-fatal on failure.

        ``steam_id = -1`` is the negative sentinel (title has no Steam
        counterpart) — same convention MetadataService writes, read by
        the sync partition to skip the game next run.
        """
        if self._cache is None or steam_id == 0:
            return
        try:
            if self._deferred_writes:
                self._cache.set(
                    "steam_real_appid", str(shortcut_app_id), steam_id,
                    flush=False,
                )
            else:
                self._cache.set(
                    "steam_real_appid", str(shortcut_app_id), steam_id,
                )
        except Exception as e:
            logger.debug(
                "[CompatLibrary] steam_real_appid backfill %r failed: %s",
                shortcut_app_id, e,
            )
