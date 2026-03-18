"""
Microsoft / Xbox Cloud Gaming connector for Unifideck.

Authenticates via Microsoft OAuth + Xbox Live token chain, checks for an
active Game Pass subscription, then syncs the full xCloud catalog.  Games
are launched via Xbox Cloud Gaming (streaming) in the Steam CEF browser
at ``https://www.xbox.com/play/launch/{productId}``.

If the user has no Game Pass subscription, a warning notification is
shown and no games are synced.

Auth flow
---------
  1. Microsoft OAuth (microsoftonline.com) → access_token + refresh_token
  2. XBL user token  (user.auth.xboxlive.com)
  3. XSTS token      (xsts.auth.xboxlive.com, RP = xboxlive.com)
  4. Game Pass subscription check (catalog.gamepass.com, signed catalog)
  5. xCloud catalog  (catalog.gamepass.com, public ~500+ games)
  6. Title resolution (displaycatalog.mp.microsoft.com, batch title lookup)

Locale
------
API calls use the locale from Unifideck's central ``settings.json``;
see ``utils/locale.py``.
"""

import asyncio
import json
import logging
import os
import time
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple

from .base import Store, Game
from .microsoft_auth import (
    http_post, http_get, build_xbl_chain,
)
from .microsoft_pipeline import (
    get_fe3_download_urls, download_file, extract_package, find_executable,
)
from .microsoft_cdp import intercept_oauth_code

logger = logging.getLogger(__name__)

# ──────────────────────────── constants ────────────────────────────────────

# OAuth endpoints, URLs, paths, and User-Agent strings are all read from
# settings.json via _get_required_setting() / _get_ms_setting().
# Only internal logic constants remain here.

MS_MARKER_FILE  = ".unifideck-ms-id"


# ──────────────────────────── connector ────────────────────────────────────

class MicrosoftConnector(Store):
    """
    Microsoft Store / Xbox Live library connector.

    Surfaces titles from the Xbox Title Hub that have a valid MS Store
    BigId and declare PC device compatibility — the subset most likely
    to work (or be attempted) via Proton on SteamOS.

    Win32 games can be downloaded and installed via the FE3 delivery API.
    UWP-only titles are surfaced but marked as not compatible.
    """

    def __init__(self, plugin_dir: Optional[str] = None, plugin_instance=None):
        """Initialise the Microsoft Store connector.

        Args:
            plugin_dir: Path to the Decky plugin directory (for settings.json lookup).
            plugin_instance: Reference to the main plugin (for sync_libraries callbacks).
        """
        self.plugin_dir      = plugin_dir
        self.plugin_instance = plugin_instance

        self._ms_access_token:  Optional[str] = None
        self._ms_refresh_token: Optional[str] = None
        self._token_saved_at:   float = 0.0

        self._xsts_token: Optional[str] = None
        self._user_hash:  Optional[str] = None
        self._xuid:       Optional[str] = None

        self._settings_cache: Optional[Dict[str, Any]] = None
        self._game_metadata: Dict[str, dict] = {}
        # xCloud subscription status (set during get_library)
        self._no_subscription: bool = False
        self._load_tokens()
        logger.info("[MS] MicrosoftConnector initialised")

    # ── Locale helpers ───────────────────────────────────────────────────

    def _get_locale(self) -> str:
        """Return the BCP-47 locale from Unifideck settings (e.g. 'fr-FR')."""
        from ..utils.locale import get_unifideck_locale
        return get_unifideck_locale()

    def _get_market(self) -> str:
        """Return the ISO 3166-1 alpha-2 market code (e.g. 'FR')."""
        from ..utils.locale import get_unifideck_market
        return get_unifideck_market()

    def _fe3_device_attrs(self) -> str:
        """Build the FE3 device-attribute string with the user's locale."""
        template = self._get_required_setting("fe3_device_attrs_template")
        return template.format(locale=self._get_locale())

    # ── Settings helpers ─────────────────────────────────────────────────

    def _load_settings(self) -> Dict[str, Any]:
        """Load and merge ``stores.microsoft`` from all settings.json files.

        Merges in reverse priority order (defaults → plugin root → user) so
        that user values override defaults.  The result is cached in
        ``_settings_cache`` to avoid re-reading files on every getter call.
        """
        merged: Dict[str, Any] = {}
        paths = []
        if self.plugin_dir:
            paths.append(os.path.join(self.plugin_dir, "defaults", "settings.json"))
            paths.append(os.path.join(self.plugin_dir, "settings.json"))
        paths.append(os.path.expanduser("~/.local/share/unifideck/settings.json"))

        for path in paths:
            try:
                if os.path.exists(path):
                    with open(path) as f:
                        data = json.load(f)
                    section = data.get("stores", {}).get("microsoft", {})
                    merged.update(section)
            except Exception as e:
                logger.debug(f"[MS] Could not read settings from {path}: {e}")

        self._settings_cache = merged
        logger.debug(f"[MS] Settings loaded ({len(merged)} keys)")
        return merged

    def _reload_settings(self) -> None:
        """Force re-read of settings.json on next access."""
        self._settings_cache = None

    def _get_ms_setting(self, key: str, default: str = "") -> str:
        """Read ``stores.microsoft.<key>`` from the cached settings.

        The cache is populated on first access via _load_settings().
        Call _reload_settings() to force a re-read from disk.
        """
        if self._settings_cache is None:
            self._load_settings()
        val = self._settings_cache.get(key, "")
        if val:
            return str(val)
        return default

    def _get_required_setting(self, key: str) -> str:
        """Read a required ``stores.microsoft.<key>`` — logs an error if missing."""
        val = self._get_ms_setting(key)
        if not val:
            label = key.replace("_", " ")
            logger.error(f"[MS] Missing '{key}' in settings.json — {label} will fail.")
        return val

    # Required settings — each reads stores.microsoft.<key> from settings.json.
    # See _get_required_setting() for the search order (user → plugin → defaults).

    def _get_client_id(self) -> str:         return self._get_required_setting("client_id")
    def _get_auth_url(self) -> str:          return self._get_required_setting("auth_url")
    def _get_token_url(self) -> str:         return self._get_required_setting("token_url")
    def _get_redirect_uri(self) -> str:      return self._get_required_setting("redirect_uri")
    def _get_scope(self) -> str:             return self._get_required_setting("scope")
    def _get_xbl_auth_url(self) -> str:      return self._get_required_setting("xbl_auth_url")
    def _get_xsts_url(self) -> str:          return self._get_required_setting("xsts_url")
    def _get_product_url(self) -> str:       return self._get_required_setting("product_url")
    def _get_titlehub_url(self) -> str:      return self._get_required_setting("titlehub_url")
    def _get_fe3_url(self) -> str:           return self._get_required_setting("fe3_url")
    def _get_xbl_user_agent(self) -> str:    return self._get_required_setting("xbl_user_agent")
    def _get_catalog_user_agent(self) -> str: return self._get_required_setting("catalog_user_agent")
    def _get_cdn_user_agent(self) -> str:    return self._get_required_setting("cdn_user_agent")
    def _get_xcloud_catalog_id(self) -> str: return self._get_required_setting("xcloud_catalog_id")
    def _get_gamepass_catalog_url(self) -> str: return self._get_required_setting("gamepass_catalog_url")

    # Settings with special handling (path expansion, type conversion).

    def _get_token_file(self) -> str:
        """Filesystem path for persisted OAuth tokens (with ~ expansion)."""
        return os.path.expanduser(self._get_required_setting("token_file"))

    def _get_install_dir(self) -> str:
        """Root directory for downloaded MS Store games (with ~ expansion)."""
        return os.path.expanduser(self._get_required_setting("install_dir"))

    def _get_token_refresh_threshold(self) -> int:
        """Max token age (seconds) before proactive refresh.  Default 2400."""
        raw = self._get_ms_setting("token_refresh_threshold", "2400")
        try:
            return int(raw)
        except (ValueError, TypeError):
            logger.warning(f"[MS] Invalid token_refresh_threshold {raw!r}, using 2400")
            return 2400

    def _validated_install_dir(self, game_id: str) -> str:
        """Return install path for *game_id*, rejecting path-traversal attempts."""
        base = self._get_install_dir()
        install_dir = os.path.join(base, game_id)
        if not os.path.abspath(install_dir).startswith(
            os.path.abspath(base) + os.sep
        ):
            raise ValueError(
                f"[MS] Refusing to use install path outside install_dir: {install_dir!r}"
            )
        return install_dir

    # ── Store interface ──────────────────────────────────────────────────

    @property
    def store_name(self) -> str:
        """Unique identifier for this store connector."""
        return "microsoft"

    async def is_available(self) -> bool:
        """Return True if we have a saved (and refreshable) token."""
        if not os.path.exists(self._get_token_file()):
            return False
        try:
            with open(self._get_token_file()) as f:
                data = json.load(f)
            return bool(data.get("refresh_token"))
        except Exception:
            return False

    async def _clear_ms_cookies(self) -> None:
        """Clear Microsoft login cookies from CEF via CDP."""
        try:
            from ..auth.browser import CDPOAuthMonitor
            monitor = CDPOAuthMonitor()
            for domain in ("login.live.com", "live.com", "microsoft.com", "login.microsoftonline.com"):
                await monitor.clear_cookies_for_domain(domain)
        except Exception as e:
            logger.debug(f"[MS] Cookie clear (non-fatal): {e}")

    async def start_auth(self) -> Dict[str, Any]:
        """Build the Microsoft OAuth URL and launch CDP monitoring."""
        await self._clear_ms_cookies()
        logger.info("[MS] Cleared Microsoft cookies before auth")

        auth_url = (
            f"{self._get_auth_url()}"
            f"?client_id={self._get_client_id()}"
            f"&redirect_uri={urllib.parse.quote(self._get_redirect_uri())}"
            f"&response_type=code"
            f"&scope={urllib.parse.quote(self._get_scope())}"
        )
        self._pending_auth_url = auth_url

        if hasattr(self, "_auth_monitor_task") and self._auth_monitor_task and not self._auth_monitor_task.done():
            self._auth_monitor_task.cancel()
        self._auth_monitor_task = asyncio.create_task(self._monitor_and_complete_auth())

        return {
            "success": True,
            "url":     auth_url,
            "message": "Sign in with your Microsoft / Xbox account",
        }

    async def complete_auth(self, auth_code: str) -> Dict[str, Any]:
        """Exchange the OAuth code for MS tokens and persist them."""
        try:
            token_data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: http_post(
                    self._get_token_url(),
                    {
                        "client_id":    self._get_client_id(),
                        "redirect_uri": self._get_redirect_uri(),
                        "code":         auth_code,
                        "grant_type":   "authorization_code",
                        "scope":        self._get_scope(),
                    },
                    {"Content-Type": "application/x-www-form-urlencoded"},
                ),
            )
            if "access_token" not in token_data:
                return {"success": False, "error": "Token exchange failed: " + str(token_data)}

            self._ms_access_token  = token_data["access_token"]
            self._ms_refresh_token = token_data.get("refresh_token", "")
            self._token_saved_at   = time.time()
            self._save_tokens()

            logger.info("[MS] ✓ Authentication complete")
            return {"success": True, "message": "Microsoft account connected"}

        except Exception as e:
            logger.error(f"[MS] complete_auth error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def logout(self) -> Dict[str, Any]:
        """Clear stored tokens and browser cookies."""
        self._ms_access_token  = None
        self._ms_refresh_token = None
        self._xsts_token       = None
        self._user_hash        = None
        self._xuid             = None
        try:
            if os.path.exists(self._get_token_file()):
                os.remove(self._get_token_file())
        except Exception as e:
            logger.warning(f"[MS] Could not remove token file: {e}")

        await self._clear_ms_cookies()

        return {"success": True, "message": "Logged out from Microsoft Store"}

    # ── Library sync ─────────────────────────────────────────────────────

    async def get_library(self) -> List[Game]:
        """Fetch xCloud-playable games for the authenticated user.

        Flow:
          1. Refresh tokens and build XBL/XSTS chain.
          2. Check Game Pass subscription via signed catalog.
          3. If no subscription → set _no_subscription flag, return [].
          4. Fetch the full xCloud catalog (public API, ~500+ games).
          5. Batch-query displaycatalog for game titles.
          6. Return Game objects tagged "xcloud" (launchable via browser).
        """
        self._no_subscription = False

        if not await self.is_available():
            if not os.path.exists(self._get_token_file()):
                logger.error(
                    "[MS] Not authenticated — token file does not exist. "
                    "Authenticate via Quick Access Menu → Unifideck → Microsoft."
                )
            else:
                try:
                    with open(self._get_token_file()) as f:
                        data = json.load(f)
                    has_refresh = bool(data.get("refresh_token"))
                    logger.error(
                        f"[MS] Not authenticated — token file exists but "
                        f"refresh_token={'present' if has_refresh else 'MISSING'}. "
                        f"Re-authenticate to fix."
                    )
                except Exception as e:
                    logger.error(f"[MS] Not authenticated — token file unreadable: {e}")
            return []

        try:
            # ── 1. Refresh MS access token if stale ──────────────────────
            token_ok = await self._ensure_fresh_ms_token()
            if not token_ok:
                logger.error("[MS] Session expired — re-authenticate via Unifideck → Microsoft.")
                return []

            # ── 2. XBL / XSTS token chain ────────────────────────────────
            ok = await asyncio.get_event_loop().run_in_executor(
                None, self._build_xbl_chain
            )
            if not ok:
                logger.warning("[MS] Could not build XBL/XSTS token chain")

            # ── 3. Check Game Pass subscription ──────────────────────────
            has_gamepass = await asyncio.get_event_loop().run_in_executor(
                None, self._check_gamepass_subscription
            )
            if not has_gamepass:
                logger.info("[MS] No active Game Pass subscription detected")
                self._no_subscription = True
                return []

            # ── 4. Fetch xCloud catalog ──────────────────────────────────
            xcloud_ids = await asyncio.get_event_loop().run_in_executor(
                None, self._fetch_xcloud_catalog
            )
            if not xcloud_ids:
                logger.warning("[MS] xCloud catalog is empty or unreachable")
                return []

            # ── 5. Batch-resolve titles from displaycatalog ──────────────
            titles = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._batch_get_titles(xcloud_ids)
            )

            # ── 6. Build Game objects ────────────────────────────────────
            games: List[Game] = []
            for pid in xcloud_ids:
                title = titles.get(pid, pid)
                game = Game(
                    id=pid,
                    title=title,
                    store="microsoft",
                    is_installed=False,
                    store_tags=["xcloud"],
                )
                games.append(game)

            logger.info(f"[MS] Returning {len(games)} xCloud games")
            return games

        except Exception as e:
            logger.error(f"[MS] Error fetching library: {e}", exc_info=True)
            return []

    # ── Token management ─────────────────────────────────────────────────

    def _load_tokens(self) -> None:
        """Load persisted OAuth tokens from self._get_token_file() into memory."""
        try:
            if os.path.exists(self._get_token_file()):
                with open(self._get_token_file()) as f:
                    data = json.load(f)
                self._ms_access_token  = data.get("access_token")
                self._ms_refresh_token = data.get("refresh_token")
                self._token_saved_at   = data.get("saved_at", 0.0)
                logger.info("[MS] Loaded tokens from disk")
        except Exception as e:
            logger.warning(f"[MS] Could not load tokens: {e} from disk")

    def _save_tokens(self) -> None:
        """Persist tokens to disk with restricted permissions (0o600)."""
        try:
            os.makedirs(os.path.dirname(self._get_token_file()), exist_ok=True)
            fd = os.open(self._get_token_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(
                    {
                        "access_token":  self._ms_access_token,
                        "refresh_token": self._ms_refresh_token,
                        "saved_at":      self._token_saved_at,
                        "scope":         self._get_scope(),
                    },
                    f,
                )
        except Exception as e:
            logger.warning(f"[MS] Could not save tokens: {e}")

    async def _ensure_fresh_ms_token(self) -> bool:
        """Proactively refresh the MS access token if it is near expiry.

        Returns True if the token is usable (fresh or successfully refreshed).
        Returns False and auto-logs-out if the session is unrecoverable
        (missing refresh_token or Microsoft rejected it).
        """
        age = time.time() - self._token_saved_at
        if age < self._get_token_refresh_threshold():
            return True
        if not self._ms_refresh_token:
            logger.error("[MS] No refresh token — session expired. Logging out.")
            await self.logout()
            return False
        try:
            logger.info(f"[MS] Refreshing MS access token (age={age:.0f}s)")
            token_data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: http_post(
                    self._get_token_url(),
                    {
                        "client_id":     self._get_client_id(),
                        "redirect_uri":  self._get_redirect_uri(),
                        "refresh_token": self._ms_refresh_token,
                        "grant_type":    "refresh_token",
                        "scope":         self._get_scope(),
                    },
                    {"Content-Type": "application/x-www-form-urlencoded"},
                ),
            )
            if "access_token" in token_data:
                self._ms_access_token  = token_data["access_token"]
                self._ms_refresh_token = token_data.get("refresh_token", self._ms_refresh_token)
                self._token_saved_at   = time.time()
                self._save_tokens()
                logger.info("[MS] ✓ Access token refreshed")
                return True

            error_code = token_data.get("error", "unknown")
            logger.error(f"[MS] Token refresh rejected ({error_code}). Logging out.")
            await self.logout()
            return False

        except Exception as e:
            logger.error(f"[MS] Token refresh error: {e}", exc_info=True)
            return False

    # ── XBL token chain ──────────────────────────────────────────────────

    def _build_xbl_chain(self) -> bool:
        """Delegate to the pure function in microsoft_auth.

        Reads XBL/XSTS endpoint URLs from settings.json and passes them
        as explicit parameters to the pure function.
        """
        self._xsts_token = None
        self._user_hash  = None

        result = build_xbl_chain(
            self._ms_access_token,
            self._get_locale(),
            xbl_auth_url=self._get_xbl_auth_url(),
            xsts_url=self._get_xsts_url(),
            xbl_user_agent=self._get_xbl_user_agent(),
        )
        if result is None:
            return False

        self._user_hash  = result["user_hash"]
        self._xsts_token = result["xsts_token"]
        self._xuid       = result["xuid"]
        return True

    # ── Title Hub API (synchronous, run in executor) ─────────────────────


    # ── xCloud / Game Pass ───────────────────────────────────────────────

    def _fetch_xcloud_catalog(self) -> List[str]:
        """Fetch the list of product IDs available on Xbox Cloud Gaming.

        Uses the public Game Pass catalog API — no auth required.

        Returns:
            List of product IDs (BigIds) playable via xCloud.
        """
        catalog_url = self._get_gamepass_catalog_url()
        catalog_id  = self._get_xcloud_catalog_id()
        url = (
            f"{catalog_url}?id={catalog_id}"
            f"&language={self._get_locale()}"
            f"&market={self._get_market()}"
        )
        try:
            data = http_get(url, {"User-Agent": self._get_catalog_user_agent()})
            # First entry is catalog metadata, rest are game entries
            ids = [item["id"] for item in data if item.get("id")]
            logger.info(f"[MS] xCloud catalog: {len(ids)} games available")
            return ids
        except Exception as e:
            logger.error(f"[MS] Failed to fetch xCloud catalog: {e}")
            return []

    def _check_gamepass_subscription(self) -> bool:
        """Check if the user has an active Game Pass subscription.

        Attempts to query the signed-in Game Pass catalog with XSTS auth.
        The signed-in endpoint returns personalized data for subscribers;
        non-subscribers receive a 401/403 or empty result.

        Returns:
            True if the user appears to have an active Game Pass subscription.
        """
        if not self._xsts_token or not self._user_hash:
            logger.warning("[MS] Cannot check subscription — no XSTS token")
            return False

        auth = f"XBL3.0 x={self._user_hash};{self._xsts_token}"
        catalog_url = self._get_gamepass_catalog_url()
        # Use the "signed-in" Game Pass PC catalog
        url = (
            f"{catalog_url}"
            f"?id=fdd9e2a7-0fee-49f6-ad69-4354098401ff"
            f"&language={self._get_locale()}"
            f"&market={self._get_market()}"
        )
        try:
            data = http_get(url, {
                "Authorization": auth,
                "User-Agent":    self._get_catalog_user_agent(),
            })
            # Catalog returns a list; first entry is metadata, rest are games
            game_count = sum(1 for item in data if item.get("id"))
            logger.info(f"[MS] Game Pass subscription check: {game_count} games accessible")
            return game_count > 0
        except Exception as e:
            logger.info(f"[MS] Game Pass subscription check failed: {e}")
            return False

    def _batch_get_titles(self, product_ids: List[str]) -> Dict[str, str]:
        """Batch-fetch game titles from the displaycatalog API.

        Args:
            product_ids: List of product IDs (BigIds) to look up.

        Returns:
            Dict mapping productId → title string.
        """
        result: Dict[str, str] = {}
        batch_size = 20

        for i in range(0, len(product_ids), batch_size):
            batch = product_ids[i: i + batch_size]
            ids_param = ",".join(batch)
            url = (
                f"{self._get_product_url()}"
                f"?bigIds={ids_param}"
                f"&market={self._get_market()}"
                f"&languages={self._get_locale()}"
                f"&fieldsTemplate=Browse"
            )
            try:
                data = http_get(url, {
                    "Accept":     "application/json",
                    "User-Agent": self._get_catalog_user_agent(),
                    "MS-CV":      "unifideck.xcloud",
                })
                for product in data.get("Products", []):
                    pid   = product.get("ProductId", "")
                    title = ""
                    for loc in product.get("LocalizedProperties", []):
                        title = loc.get("ProductTitle", "")
                        if title:
                            break
                    if pid and title:
                        result[pid] = title
            except Exception as e:
                logger.warning(
                    f"[MS] xCloud title batch {i // batch_size} failed: {e}"
                )

        logger.info(f"[MS] Resolved {len(result)} titles from {len(product_ids)} product IDs")
        return result

    def _query_titlehub(self) -> List[Dict]:
        """Query the Xbox Title Hub API for the user's game library."""
        if not self._xsts_token or not self._user_hash or not self._xuid:
            logger.error("[MS] Cannot query Title Hub — XSTS token, user hash, or XUID missing")
            return []

        auth_header = f"XBL3.0 x={self._user_hash};{self._xsts_token}"
        titlehub_url = self._get_titlehub_url()
        url = (
            f"{titlehub_url}/users/xuid({self._xuid})/titles/titlehistory"
            f"/decoration/detail,image,scid"
        )
        headers = {
            "Authorization":          auth_header,
            "x-xbl-contract-version": "2",
            "Accept":                 "application/json",
            "Accept-Language":        self._get_locale(),
        }

        try:
            data = http_get(url, headers)
        except Exception as e:
            logger.error(f"[MS] Title Hub query failed: {e}")
            return []

        titles = data.get("titles", [])
        logger.info(f"[MS] Title Hub raw: {len(titles)} titles")

        items: List[Dict] = []
        for t in titles:
            devices = [d.lower() for d in t.get("devices", [])]
            if "pc" not in devices and "win32" not in devices:
                continue
            if t.get("type", "").lower() != "game":
                continue
            # Extract the MS Store BigId from detail.availabilities.
            # The modernTitleId is a numeric Xbox ID that the catalog
            # API does not accept — we need the alphanumeric ProductId
            # (e.g. "9NBLGGH3ZB9T") from the availability data.
            title_id = t.get("modernTitleId") or t.get("titleId") or ""
            big_id = ""
            for avail in t.get("detail", {}).get("availabilities", []):
                big_id = avail.get("ProductId", "")
                if big_id:
                    break
            if not big_id:
                logger.debug(f"[MS] Title Hub: no BigId for {t.get('name', '?')} (titleId={title_id}) — skipping")
                continue
            items.append({
                "productId":       big_id,
                "titleId":         str(title_id),
                "productTitle":    t.get("name", ""),
                "pfn":             t.get("pfn", ""),
            })

        logger.info(f"[MS] Title Hub: {len(items)} PC games after device/type filter")
        return items



    # ── Product detail + PC filter ───────────────────────────────────────

    def _scan_pc_games(self, items: List[Dict]) -> Dict[str, dict]:
        """Batch-scan products via displaycatalog and classify Win32 vs UWP.

        Queries the catalog by BigId (bigIds= parameter) extracted from the
        Title Hub detail.availabilities data.

        Args:
            items: List of dicts from _query_titlehub, each with
                   'productId' (BigId from catalog) and 'titleId' (Xbox numeric ID).

        Returns:
            Dict mapping productId (BigId) → classification metadata.
        """
        if not items:
            return {}

        big_ids = [item["productId"] for item in items if item.get("productId")]
        if not big_ids:
            logger.warning("[MS] No BigId available for any game — cannot query product catalog")
            return {}

        logger.info(f"[MS] Scanning {len(big_ids)} product(s) by BigId for Win32/UWP classification")
        result: Dict[str, dict] = {}
        batch_size = 20

        for i in range(0, len(big_ids), batch_size):
            batch = big_ids[i: i + batch_size]
            ids_param = ",".join(batch)
            url = (
                f"{self._get_product_url()}"
                f"?bigIds={ids_param}"
                f"&market={self._get_market()}"
                f"&languages={self._get_locale()}"
            )

            try:
                data     = http_get(url, {"Accept": "application/json", "User-Agent": self._get_catalog_user_agent(), "MS-CV": "unifideck.2"})
                products = data.get("Products", [])
                logger.info(f"[MS] Product scan batch {i // batch_size}: {len(products)} products returned for {len(batch)} queried")

                for product in products:
                    pid = product.get("ProductId", "")
                    if not pid:
                        continue
                    _is_win32, meta = self._classify_product(product)
                    result[pid] = meta

            except Exception as e:
                _body = ""
                if hasattr(e, "read"):
                    try: _body = e.read().decode("utf-8", errors="replace")[:500]
                    except Exception: pass
                logger.warning(f"[MS] Product scan failed for batch {i // batch_size}: {e}")
                if _body:
                    logger.warning(f"[MS] Product scan error body: {_body}")

        return result


    def _classify_product(self, product: dict) -> Tuple[bool, dict]:
        """Determine whether a product is downloadable via FE3.

        Classification uses the FulfillmentType field from the SKU properties:
          - 'MSIXVC': Win32 game in MSIX Virtual Container — installable
          - 'XVC': Xbox Virtual Container (UWP-only) — not installable on Linux
          - None: delisted or unavailable — attempt download, will fail gracefully

        In 2024+, Microsoft packages all games (including Win32) in MSIX
        containers with a PackageFamilyName, so the presence of a PFN no
        longer indicates UWP-only.
        """
        meta: dict = {
            "is_win32":         False,
            "wu_bundle_id":     "",
            "wu_category_id":   "",
            "title":            "",
            "is_play_anywhere": False,
        }

        for sku_avail in product.get("DisplaySkuAvailabilities", []):
            loc_props = sku_avail.get("Sku", {}).get("LocalizedProperties", [])
            if loc_props:
                meta["title"] = loc_props[0].get("SkuTitle", "")
                break

        for sku_avail in product.get("DisplaySkuAvailabilities", []):
            sku   = sku_avail.get("Sku", {})
            props = sku.get("Properties", {})
            fd    = props.get("FulfillmentData", {}) or {}

            if props.get("IsXboxPlayAnywhere"):
                meta["is_play_anywhere"] = True

            wu_bundle        = fd.get("WuBundleId", "")
            wu_cat           = fd.get("WuCategoryId", "")
            fulfillment_type = props.get("FulfillmentType", "")

            # XVC = Xbox Virtual Container (UWP-only) — skip
            if wu_bundle and fulfillment_type != "XVC":
                meta["is_win32"]       = True
                meta["wu_bundle_id"]   = wu_bundle
                meta["wu_category_id"] = wu_cat
                return True, meta

        return False, meta

    def _fetch_single_product_meta(self, game_id: str) -> Optional[dict]:
        """Fetch and classify a single product from the catalog API.

        Returns the metadata dict if the product is Win32, else None.
        """
        logger.debug(f"[MS] Fetching product metadata for {game_id}")
        try:
            url = f"{self._get_product_url()}?bigIds={game_id}&market={self._get_market()}&languages={self._get_locale()}"
            data = http_get(url, {"Accept": "application/json", "User-Agent": self._get_catalog_user_agent(), "MS-CV": "unifideck.3"})
            for product in data.get("Products", []):
                is_win32, meta = self._classify_product(product)
                if is_win32:
                    return meta
        except Exception as e:
            logger.error(f"[MS] Single product fetch failed for {game_id}: {e}")
        return None

    # ── Installed games tracking ─────────────────────────────────────────

    def get_installed(self) -> Dict[str, dict]:
        """Scan the MS install directory for installed Win32 games.

        Returns:
            Dict mapping game_id → {'install_path', 'executable', 'installed_at'}
        """
        installed: Dict[str, dict] = {}
        base_dir = self._get_install_dir()
        if not os.path.exists(base_dir):
            return installed
        try:
            for entry in os.listdir(base_dir):
                entry_path = os.path.join(base_dir, entry)
                if not os.path.isdir(entry_path):
                    continue
                marker = os.path.join(entry_path, MS_MARKER_FILE)
                if not os.path.exists(marker):
                    continue
                try:
                    with open(marker) as f:
                        data = json.load(f)
                    game_id = data.get("game_id", entry)
                    installed[game_id] = {
                        "install_path":  entry_path,
                        "executable":    data.get("executable", ""),
                        "installed_at":  data.get("installed_at", 0),
                    }
                except Exception as e:
                    logger.warning(f"[MS] Could not read marker {marker}: {e}")
        except Exception as e:
            logger.error(f"[MS] Error scanning install dir: {e}")
        return installed

    def get_installed_game_info(self, game_id: str) -> Optional[dict]:
        """Return install info for a single game, or None if not installed."""
        return self.get_installed().get(game_id)

    # ── Installation ─────────────────────────────────────────────────────

    async def install_game(self, game_id: str, progress_callback=None) -> dict:
        """Download and install a Win32 MS Store game via FE3."""
        if not await self.is_available():
            return {"success": False, "error": "Not authenticated with Microsoft"}

        meta = self._game_metadata.get(game_id)
        if not meta:
            meta = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._fetch_single_product_meta(game_id),
            )
            if meta:
                self._game_metadata[game_id] = meta

        if not meta or not meta.get("is_win32"):
            return {"success": False, "error": "Game is UWP-only and cannot be installed on Linux"}

        wu_bundle_id = meta.get("wu_bundle_id", "")
        if not wu_bundle_id:
            return {"success": False, "error": "No download bundle ID found for this game"}

        try:
            install_dir = self._validated_install_dir(game_id)
        except ValueError as e:
            logger.error(str(e))
            return {"success": False, "error": "Invalid game identifier"}

        try:
            token_ok = await self._ensure_fresh_ms_token()
            if not token_ok:
                logger.error("[MS] Session expired — logging out automatically")
                await self.logout()
                return {"success": False, "error": "Session expired — please re-authenticate"}
            ok = await asyncio.get_event_loop().run_in_executor(None, self._build_xbl_chain)
            if not ok:
                return {"success": False, "error": "Failed to build Xbox authentication chain"}

            os.makedirs(install_dir, exist_ok=True)

            if progress_callback:
                await progress_callback({"phase": "preparing", "phase_message": "Requesting download links…"})

            urls = await asyncio.get_event_loop().run_in_executor(
                None, lambda: get_fe3_download_urls(
                    wu_bundle_id, self._xsts_token, self._user_hash,
                    self._xuid, self._fe3_device_attrs(), self._get_fe3_url()
                )
            )
            if not urls:
                return {"success": False, "error": "Microsoft delivery service returned no download URLs"}

            logger.info(f"[MS] Got {len(urls)} download URL(s) for {game_id}")

            exe_path = await self._download_and_install(urls, install_dir, game_id, progress_callback)
            if not exe_path:
                return {"success": False, "error": "Installation complete but no executable found — check logs"}

            marker_data = {
                "game_id":      game_id,
                "install_path": install_dir,
                "executable":   exe_path,
                "installed_at": time.time(),
            }
            with open(os.path.join(install_dir, MS_MARKER_FILE), "w") as f:
                json.dump(marker_data, f, indent=2)

            logger.info(f"[MS] ✓ {game_id} installed at {install_dir}, exe={exe_path}")
            return {"success": True, "install_path": install_dir, "executable": exe_path}

        except Exception as e:
            logger.error(f"[MS] install_game error for {game_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def uninstall_game(self, game_id: str) -> dict:
        """Remove installed game files and the marker."""
        import shutil
        try:
            install_dir = self._validated_install_dir(game_id)
        except ValueError as e:
            logger.error(str(e))
            return {"success": False, "error": "Invalid game identifier"}
        if not os.path.exists(install_dir):
            return {"success": False, "error": "Game not found in install directory"}
        try:
            shutil.rmtree(install_dir)
            logger.info(f"[MS] Uninstalled {game_id}")
            return {"success": True}
        except Exception as e:
            logger.error(f"[MS] Uninstall error for {game_id}: {e}")
            return {"success": False, "error": str(e)}

    # ── Download + extract (delegates to microsoft_pipeline) ─────────────

    async def _download_and_install(
        self, urls: List[str], install_dir: str,
        game_id: str, progress_callback=None,
    ) -> Optional[str]:
        """Download all package URLs, extract them, and return the exe path."""
        import tempfile
        import shutil

        logger.info(f"[MS] Downloading {len(urls)} package(s) for {game_id}")
        tmp_dir = tempfile.mkdtemp(prefix=f"unifideck_ms_{game_id}_")
        try:
            downloaded: List[str] = []
            total = len(urls)

            for idx, url in enumerate(urls):
                raw_name = urllib.parse.unquote(url.split("?")[0].split("/")[-1])
                filename = os.path.basename(raw_name) or f"package_{idx}.appx"
                dest     = os.path.join(tmp_dir, filename)

                if progress_callback:
                    pct = int((idx / total) * 70)
                    await progress_callback({
                        "phase":         "downloading",
                        "phase_message": f"Downloading package {idx + 1}/{total}…",
                        "progress_percent": pct,
                    })

                cdn_ua = self._get_cdn_user_agent()
                ok = await asyncio.get_event_loop().run_in_executor(
                    None, lambda u=url, d=dest, ua=cdn_ua: download_file(u, d, ua)
                )
                if ok:
                    downloaded.append(dest)

            if not downloaded:
                logger.error("[MS] No packages downloaded successfully")
                return None

            if progress_callback:
                await progress_callback({"phase": "extracting", "phase_message": "Extracting…", "progress_percent": 75})

            for pkg in downloaded:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda p=pkg: extract_package(p, install_dir)
                )

            if progress_callback:
                await progress_callback({"phase": "verifying", "phase_message": "Locating executable…", "progress_percent": 95})

            return find_executable(install_dir)

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── CDP auto-auth monitor ────────────────────────────────────────────

    async def _monitor_and_complete_auth(self) -> None:
        """Background task: intercept the OAuth redirect via CDP Network events."""
        try:
            code = await intercept_oauth_code(
                pending_auth_url=getattr(self, "_pending_auth_url", ""),
                timeout=300,
            )
            if code:
                logger.info("[MS] ✓ Received OAuth code via Network interception")
                result = await self.complete_auth(code)
                if result["success"]:
                    logger.info("[MS] ✓ Authentication completed")
                else:
                    logger.error(f"[MS] complete_auth failed: {result.get('error')}")
            else:
                logger.warning("[MS] Network interception timed out — no code received")
        except Exception as e:
            logger.error(f"[MS] Auth monitor error: {e}", exc_info=True)
