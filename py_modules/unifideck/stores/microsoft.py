"""
Microsoft / Xbox Cloud Gaming connector for Unifideck.

Authenticates via Microsoft OAuth + Xbox Live token chain, then syncs
the public xCloud catalog.  Games are launched via Xbox Cloud Gaming
(streaming) in Chromium at ``https://www.xbox.com/play/launch/{productId}``.

Subscription validation is handled by xbox.com at game launch time — no
client-side check is needed.

Auth flow
---------
  1. Microsoft OAuth (microsoftonline.com) → access_token + refresh_token
  2. XBL user token  (user.auth.xboxlive.com)
  3. XSTS token      (xsts.auth.xboxlive.com, RP = xboxlive.com)
  4. xCloud catalog  (catalog.gamepass.com, public ~500+ games)
  5. Title resolution (displaycatalog.mp.microsoft.com, batch title lookup)

Browser management is delegated to ``microsoft_chromium.ChromiumBrowser``.

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
from typing import Dict, Any, List, Optional

from .base import Store, Game
from .microsoft_auth import (
    http_post, http_get, build_xbl_chain,
)
from .microsoft_cdp import intercept_oauth_code
from .microsoft_chromium import ChromiumBrowser

logger = logging.getLogger(__name__)

# ──────────────────────────── constants ────────────────────────────────────

# OAuth endpoints, URLs, paths, and User-Agent strings are all read from
# settings.json via _get_required_setting() / _get_ms_setting().
# Only internal logic constants remain here.

MS_MARKER_FILE  = ".unifideck-ms-id"


# ──────────────────────────── connector ────────────────────────────────────

class MicrosoftConnector(Store):
    """
    Microsoft / Xbox Cloud Gaming connector.

    Syncs xCloud-playable titles from the public Game Pass catalog.
    Auth uses Chromium (managed by ``ChromiumBrowser``) for OAuth +
    CDP interception.  Games are streamed via xbox.com/play.
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
        self._load_tokens()

        # Browser manager for Chromium auth and xCloud (composition)
        self._browser = ChromiumBrowser(
            cdp_port=9222,
            locale_fn=self._get_locale,
        )
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
    def _get_xbl_user_agent(self) -> str:    return self._get_required_setting("xbl_user_agent")
    def _get_catalog_user_agent(self) -> str: return self._get_required_setting("catalog_user_agent")
    def _get_xcloud_catalog_id(self) -> str: return self._get_required_setting("xcloud_catalog_id")
    def _get_gamepass_catalog_url(self) -> str: return self._get_required_setting("gamepass_catalog_url")

    # Settings with special handling (path expansion, type conversion).

    def _get_token_file(self) -> str:
        """Filesystem path for persisted OAuth tokens (with ~ expansion)."""
        return os.path.expanduser(self._get_required_setting("token_file"))


    def _get_token_refresh_threshold(self) -> int:
        """Max token age (seconds) before proactive refresh.  Default 2400."""
        raw = self._get_ms_setting("token_refresh_threshold", "2400")
        try:
            return int(raw)
        except (ValueError, TypeError):
            logger.warning(f"[MS] Invalid token_refresh_threshold {raw!r}, using 2400")
            return 2400


    # ── Store interface ──────────────────────────────────────────────────

    @property
    def store_name(self) -> str:
        """Unique identifier for this store connector."""
        return "microsoft"

    async def is_available(self) -> bool:
        """Return True if we have a saved token AND the browser session is active."""
        if not os.path.exists(self._get_token_file()):
            return False
        try:
            with open(self._get_token_file()) as f:
                data = json.load(f)
            if not data.get("refresh_token"):
                return False
        except Exception:
            return False

        if not self._browser.has_xbox_session():
            logger.info("[MS] Xbox browser session lost — user logged out from Game Pass")
            await self.logout()
            return False

        return True



    async def start_auth(self) -> Dict[str, Any]:
        """Launch Chromium with the OAuth URL and start CDP monitoring.

        Chromium opens in fullscreen app mode; CDP intercepts the OAuth
        redirect on the debugging port.  Cookies persist in the shared
        profile so xbox.com/play reuses the session for xCloud.

        Returns:
            Dict with ``success=True``.  No ``url`` is returned because
            Chromium handles the browser window directly.
        """
        auth_url = (
            f"{self._get_auth_url()}"
            f"?client_id={self._get_client_id()}"
            f"&redirect_uri={urllib.parse.quote(self._get_redirect_uri())}"
            f"&response_type=code"
            f"&scope={urllib.parse.quote(self._get_scope())}"
        )
        self._pending_auth_url = auth_url

        # Check if Chromium is available
        if not self._browser.is_installed:
            logger.info("[MS] Chromium not installed — prompting user to install")
            return {
                "success": True,
                "needs_chromium": True,
                "message": "microsoft.chromiumRequired",
            }

        # Launch Chromium with remote debugging for CDP interception
        launched = self._browser.launch_auth(auth_url)
        if not launched:
            logger.error("[MS] Failed to launch Chromium for auth")
            return {
                "success": False,
                "error": "microsoft.chromiumInstallFailed",
            }

        # Start CDP monitor targeting Chromium's debugging port
        if hasattr(self, "_auth_monitor_task") and self._auth_monitor_task and not self._auth_monitor_task.done():
            self._auth_monitor_task.cancel()
        self._auth_monitor_task = asyncio.create_task(self._monitor_and_complete_auth())

        return {
            "success":        True,
            "chromium_auth":  True,
            "message":        "microsoft.signInMessage",
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
            return {"success": True, "message": "microsoft.accountConnected"}

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

        self._browser.clear_cookies()

        return {"success": True, "message": "microsoft.loggedOut"}


    # ── Library sync ─────────────────────────────────────────────────────

    async def get_library(self) -> List[Game]:
        """Fetch xCloud-playable games from the public catalog.

        Flow:
          1. Refresh tokens and build XBL/XSTS chain.
          2. Fetch the full xCloud catalog (public API, ~500+ games).
          3. Batch-query displaycatalog for game titles.
          4. Return Game objects tagged "xcloud" (launchable via browser).

        Note: subscription validation is handled by xbox.com/play when
        the user clicks "Play on Cloud" — no client-side check needed.
        """

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

            # ── 3. Fetch xCloud catalog ───────────────────────────────
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


    async def uninstall_game(self, game_id: str) -> dict:
        """No-op — xCloud games are streamed, not installed locally."""
        return {"success": True, "message": "microsoft.xcloudNotInstalled"}

    # ── Browser delegates (called by main.py API routes) ─────────────────

    def is_chromium_installed(self) -> bool:
        """Check if Chromium or Chrome is available."""
        return self._browser.is_installed

    async def install_chromium(self) -> Dict[str, Any]:
        """Install Chromium via flatpak."""
        return await self._browser.install()


    async def _monitor_and_complete_auth(self) -> None:
        """Background task: intercept the OAuth redirect via CDP Network events."""
        cdp_port = self._browser.cdp_port if self._browser.process else 8080

        # Wait for Chromium to start
        if self._browser.process:
            ok = await self._browser.wait_and_check_crash()
            if not ok:
                return

        # Inject virtual keyboard (Steam overlay not available outside Steam)
        await self._browser.inject_virtual_keyboard()

        try:
            code = await intercept_oauth_code(
                pending_auth_url=getattr(self, "_pending_auth_url", ""),
                timeout=300,
                cdp_port=cdp_port,
            )
            if code:
                logger.info("[MS] ✓ Received OAuth code via Network interception")
                result = await self.complete_auth(code)
                if result["success"]:
                    logger.info("[MS] ✓ Authentication completed — closing Chromium")
                else:
                    logger.error(f"[MS] complete_auth failed: {result.get('error')}")
            else:
                logger.warning("[MS] Network interception timed out — no code received")
        except Exception as e:
            logger.error(f"[MS] Auth monitor error: {e}", exc_info=True)
        finally:
            self._browser.kill()
