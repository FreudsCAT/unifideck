"""
Microsoft Store connector for Unifideck.

Authenticates via Microsoft OAuth + Xbox Live token chain, queries the
Xbox Title Hub API to list the user's game library, then filters for
PC-compatible titles.  Download/install uses the FE3 (Windows Update)
delivery endpoint.  Game Pass and UWP-only games are excluded.

Auth flow
---------
  1. Microsoft OAuth (microsoftonline.com) → access_token + refresh_token
  2. XBL user token  (user.auth.xboxlive.com)
  3. XSTS token      (xsts.auth.xboxlive.com, RP = xboxlive.com)
  4. Title Hub query  (titlehub.xboxlive.com) — user's game history
  5. Product details  (displaycatalog.mp.microsoft.com) — PC / Windows.Desktop filter

Win32 detection
---------------
A product is considered downloadable when its SKU has a ``WuBundleId``
and ``FulfillmentType`` is not ``XVC`` (Xbox Virtual Container = UWP-only).
UWP-only titles are shown but marked as ``not_compatible``.

Download flow (Win32 games)
---------------------------
  1. ``install_game()`` refreshes the OAuth token and rebuilds the XSTS chain.
  2. FE3 ``GetExtendedUpdateInfo2`` SOAP call → direct download URLs.
  3. Packages are downloaded, extracted (ZIP / bundle), main exe located.
  4. A ``.unifideck-ms-id`` JSON marker is written so the game survives re-syncs.

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
        """Fetch the user's owned (purchased) PC-compatible games."""
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
            ok = await asyncio.get_event_loop().run_in_executor(None, self._build_xbl_chain)
            if not ok:
                logger.warning("[MS] Could not build XBL/XSTS token chain")

            raw_items = await asyncio.get_event_loop().run_in_executor(
                None, self._query_titlehub
            )
            logger.info(f"[MS] Title Hub returned {len(raw_items)} items")

            # NOTE: The Title Hub API returns all games the user has ever
            # interacted with (purchases, Game Pass, Xbox overlay, etc.).
            # There is no reliable way to filter only purchased games without
            # the MS Store Collections API (requires Partner Center config).
            # Games without a BigId were already filtered in _query_titlehub.
            # Users can hide unwanted games via Steam's built-in hide feature.
            purchased = raw_items
            logger.info(f"[MS] {len(purchased)} games with valid MS Store BigId")

            if not purchased:
                return []

            game_meta = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._scan_pc_games(purchased)
            )
            if game_meta:
                win32_count = sum(1 for m in game_meta.values() if m.get("is_win32"))
                logger.info(
                    f"[MS] {len(game_meta)} products classified "
                    f"({win32_count} Win32, {len(game_meta) - win32_count} UWP)"
                )
                self._game_metadata.update(
                    {pid: m for pid, m in game_meta.items() if m.get("is_win32")}
                )
            else:
                logger.warning(
                    "[MS] Product scan returned 0 results — "
                    "returning all games without Win32/UWP classification"
                )

            # ── DIAG: Test Collections API for ownership ────────────
            all_big_ids = [item["productId"] for item in purchased if item.get("productId")]
            self._diag_collections_variants(all_big_ids)

            installed = self.get_installed()

            games: List[Game] = []
            for item in purchased:
                pid = item.get("productId", "")
                if not pid:
                    continue
                meta  = game_meta.get(pid, {})
                title = item.get("productTitle") or meta.get("title") or pid

                inst_info    = installed.get(pid)
                is_installed = inst_info is not None

                tags: List[str] = []
                if game_meta and not meta.get("is_win32"):
                    tags.append("not_compatible")
                if meta.get("is_play_anywhere"):
                    tags.append("play_anywhere")
                store_tags = tags if tags else None

                game = Game(
                    id=pid,
                    title=title,
                    store="microsoft",
                    is_installed=is_installed,
                    install_path=inst_info["install_path"] if inst_info else None,
                    executable=inst_info["executable"] if inst_info else None,
                    store_tags=store_tags,
                )
                games.append(game)

            logger.info(f"[MS] Returning {len(games)} Microsoft Store games")
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



    # ── DIAG: Collections API variants ───────────────────────────────────

    def _diag_collections_variants(self, big_ids: List[str]) -> None:
        """Test multiple Collections API approaches to find one that works."""
        if not self._user_hash or not self._xuid:
            return

        from .microsoft_auth import http_post_json, ssl_ctx_strict, build_xbl_chain
        import urllib.request

        xsts_url = self._get_xsts_url()
        xbl_ua   = self._get_xbl_user_agent()

        chain = build_xbl_chain(
            self._ms_access_token, self._get_locale(),
            xbl_auth_url=self._get_xbl_auth_url(),
            xsts_url=xsts_url, xbl_user_agent=xbl_ua,
        )
        if not chain:
            logger.info("[MS] DIAG: XBL chain failed for licensing test")
            return

        xbl_token = chain["xbl_token"]

        rp_variants = [
            "http://licensing.xboxlive.com",
            "https://licensing.xboxlive.com",
            "http://licensing.mp.microsoft.com",
        ]

        for rp in rp_variants:
            try:
                xsts_resp = http_post_json(
                    xsts_url,
                    {
                        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
                        "RelyingParty": rp,
                        "TokenType": "JWT",
                    },
                    {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "x-xbl-contract-version": "1",
                        "User-Agent": xbl_ua,
                    },
                )
                if xsts_resp.get("XErr"):
                    logger.info(f"[MS] DIAG RP={rp!r}: XErr={xsts_resp['XErr']}")
                    continue
                token = xsts_resp.get("Token", "")
                uhs = xsts_resp.get("DisplayClaims", {}).get("xui", [{}])[0].get("uhs", "")
                if not token:
                    logger.info(f"[MS] DIAG RP={rp!r}: no token")
                    continue
                logger.info(f"[MS] DIAG RP={rp!r}: ✓ token OK")

                auth = f"XBL3.0 x={uhs};{token}"
                self._diag_collections_call(auth, big_ids[:5], "with_ids", rp)
                self._diag_collections_call(auth, None, "no_ids", rp)
                self._diag_collections_call(auth, big_ids[:5], "no_filters", rp)

            except Exception as e:
                _body = ""
                if hasattr(e, "read"):
                    try: _body = e.read().decode("utf-8", errors="replace")[:300]
                    except Exception: pass
                logger.info(f"[MS] DIAG RP={rp!r}: XSTS failed: {e} {_body}")

    def _diag_collections_call(self, auth: str, big_ids: Optional[List[str]], variant: str, rp: str) -> None:
        """Single Collections API call for diagnostics."""
        import urllib.request
        from .microsoft_auth import ssl_ctx_strict

        url = "https://collections.mp.microsoft.com/v8.0/collections/b2blicensepreview"
        body_dict: dict = {
            "maxPageSize": 25,
            "excludeDuplicates": True,
            "market": "neutral",
        }
        if variant == "no_filters":
            if big_ids:
                body_dict["productSkuIds"] = [{"productId": bid} for bid in big_ids]
        elif variant == "with_ids":
            body_dict["entitlementFilters"] = ["*:Game"]
            if big_ids:
                body_dict["productSkuIds"] = [{"productId": bid} for bid in big_ids]
        elif variant == "no_ids":
            body_dict["entitlementFilters"] = ["*:Game", "*:Durable"]

        body = json.dumps(body_dict).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={
                "Authorization": auth,
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Unifideck/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx_strict()) as r:
                resp = json.loads(r.read().decode())
            items = resp.get("items", [])
            logger.info(f"[MS] DIAG collections [{variant}] RP={rp!r}: {len(items)} items")
            for idx, item in enumerate(items[:3]):
                logger.info(
                    f"[MS] DIAG [{variant}] item[{idx}]: "
                    f"productId={item.get('productId', '?')!r} "
                    f"acqType={item.get('acquisitionType', '?')!r} "
                    f"status={item.get('status', '?')!r}"
                )
        except Exception as e:
            _body = ""
            if hasattr(e, "read"):
                try: _body = e.read().decode("utf-8", errors="replace")[:300]
                except Exception: pass
            logger.info(f"[MS] DIAG collections [{variant}] RP={rp!r}: FAILED {e}")
            if _body:
                logger.info(f"[MS] DIAG [{variant}] body: {_body}")


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

    async def _close_auth_browser(self) -> None:
        """Dismiss the Microsoft OAuth popup after successful auth.

        Steam's CEF popup windows cannot be closed programmatically:
        - window.close() is blocked (popup not created by JS)
        - /json/close detaches CDP but leaves window open
        - Target.closeTarget closes the target but leaves the native window
        - Closing browserviewpopup containers crashes Big Picture

        Best we can do: navigate the MS page to a friendly "done" screen
        so the user sees a clear completion message instead of the login page.
        The popup window will close when the user navigates away or presses B.
        """
        import urllib.request as _req

        MS_DOMAINS = [
            "login.live.com", "live.com",
            "login.microsoftonline.com", "microsoftonline.com",
            "account.microsoft.com", "oauth20_desktop.srf",
        ]
        try:
            with _req.urlopen("http://127.0.0.1:8080/json", timeout=5) as r:
                pages = json.loads(r.read().decode())
        except Exception as e:
            logger.info(f"[MS-close] Could not reach CEF: {e}")
            return

        logger.info(f"[MS-close] Found {len(pages)} CEF page(s)")
        closed = 0

        for page in pages:
            url    = page.get("url", "")
            ws_url = page.get("webSocketDebuggerUrl", "")

            if not any(d in url for d in MS_DOMAINS):
                continue

            if not ws_url:
                continue

            logger.info(f"[MS-close] Navigating to done screen: {url[:80]}")
            try:
                import websockets
                async with websockets.connect(
                    ws_url, ping_interval=None, open_timeout=5
                ) as ws:
                    done_html = (
                        "data:text/html;charset=utf-8,"
                        "%3Chtml%3E%3Cbody%20style%3D%22"
                        "background%3A%23171d25%3B"
                        "color%3A%23dcdedf%3B"
                        "font-family%3A-apple-system%2Csans-serif%3B"
                        "display%3Aflex%3B"
                        "flex-direction%3Acolumn%3B"
                        "align-items%3Acenter%3B"
                        "justify-content%3Acenter%3B"
                        "height%3A100vh%3B"
                        "margin%3A0%22%3E"
                        "%3Csvg%20width%3D%2264%22%20height%3D%2264%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22%2366c0f4%22%3E"
                        "%3Cpath%20d%3D%22M9%2016.17L4.83%2012l-1.42%201.41L9%2019%2021%207l-1.41-1.41z%22%2F%3E%3C%2Fsvg%3E"
                        "%3Ch2%20style%3D%22margin-top%3A16px%22%3E"
                        "Authentication%20complete"
                        "%3C%2Fh2%3E"
                        "%3Cp%20style%3D%22color%3A%23898989%22%3E"
                        "Press%20B%20to%20close%20this%20window"
                        "%3C%2Fp%3E"
                        "%3C%2Fbody%3E%3C%2Fhtml%3E"
                    )
                    await ws.send(json.dumps({
                        "id": 1,
                        "method": "Page.navigate",
                        "params": {"url": done_html},
                    }))
                    await asyncio.wait_for(ws.recv(), timeout=3)
                    closed += 1
                    logger.info("[MS-close] Navigated to done screen OK")
            except Exception as e:
                logger.info(f"[MS-close] Navigate failed: {e}")

        logger.info(f"[MS-close] Dismissed {closed} page(s)")

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
                    # Close the browser after successful auth.
                    try:
                        logger.info("[MS] Waiting 1.5s before closing browser...")
                        await asyncio.sleep(1.5)
                        logger.info("[MS] Calling _close_auth_browser...")
                        await self._close_auth_browser()
                        logger.info("[MS] _close_auth_browser returned")
                    except Exception as close_err:
                        logger.error(f"[MS] Error closing browser: {close_err}", exc_info=True)
                else:
                    logger.error(f"[MS] complete_auth failed: {result.get('error')}")
            else:
                logger.warning("[MS] Network interception timed out — no code received")
        except Exception as e:
            logger.error(f"[MS] Auth monitor error: {e}", exc_info=True)
