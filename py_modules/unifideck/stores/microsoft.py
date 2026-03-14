"""
Microsoft Store connector for Unifideck.

Authenticates via Microsoft OAuth + Xbox Live token chain, queries the
Microsoft Collections API to list owned (purchased) Win32 games, and can
download and install them via the FE3 (Windows Update) delivery endpoint.
Game Pass titles and UWP-only games (not runnable under Proton) are excluded.

Auth flow
---------
  1. Microsoft OAuth (live.com)         → access_token + refresh_token
  2. XBL user token (user.auth.xboxlive.com)
  3. XSTS token     (xsts.auth.xboxlive.com, RP = licensing.xboxlive.com)
  4. Collections query (collections.mp.microsoft.com)
  5. Product details   (store.mp.microsoft.com) — PC / Windows.Desktop filter

Win32 detection
---------------
A product is considered downloadable (Win32) when it exposes a non-empty
``FulfillmentData.WuBundleId`` **and** lacks a ``PackageFamilyName`` (which
would indicate an MSIX/UWP package).  UWP-only titles are silently dropped
from the library.

Download flow (Win32 games)
---------------------------
  1. ``install_game()`` refreshes the OAuth token and rebuilds the XSTS chain.
  2. ``_get_fe3_download_urls()`` performs a ``GetExtendedUpdateInfo2`` SOAP
     call against the FE3 /secured endpoint to obtain direct download URLs.
  3. Packages are downloaded to a temp directory, extracted (ZIP / bundle),
     and the main executable is located by priority name then size heuristic.
  4. A ``.unifideck-ms-id`` JSON marker is written to the install directory so
     the game survives library re-syncs.

Locale
------
API calls (market=, locale= query parameters; FE3 device attributes) use the
locale from Unifideck's central ``settings.json``; see ``utils/locale.py``.
"""

import asyncio
import json
import logging
import os
import time
import urllib.parse
import urllib.request
import ssl
from typing import Dict, Any, List, Optional, Tuple

from .base import Store, Game

logger = logging.getLogger(__name__)

# ──────────────────────────── constants ────────────────────────────────────

# Public client ID used by many open-source Xbox tools (no secret required).
MS_CLIENT_ID   = "000000004C12AE6F"
MS_REDIRECT    = "https://login.live.com/oauth20_desktop.srf"
# Azure AD v2.0 consumer endpoint returns JWT tokens (not compact MSA tickets).
# JWTs are required for XBL contract-v2 which is in turn required to obtain
# an XSTS token with the licensing.xboxlive.com relying party (Collections API).
# The legacy login.live.com endpoint returns compact tokens that only work with
# XBL contract-v1 -- those tokens are rejected by the licensing XSTS RP (400).
MS_AUTH_URL    = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
MS_TOKEN_URL   = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
MS_SCOPE       = "Xboxlive.signin Xboxlive.offline_access"

XBL_AUTH_URL   = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL       = "https://xsts.auth.xboxlive.com/xsts/authorize"
XSTS_RP        = "https://licensing.xboxlive.com/"   # relying party for Collections

COLLECTIONS_URL = "https://collections.mp.microsoft.com/v8.0/collections/query"
PRODUCT_URL     = "https://store.mp.microsoft.com/v8.0/sdk/products"

TOKEN_FILE    = os.path.expanduser("~/.config/unifideck/microsoft_token.json")

# ── Win32 / FE3 download constants ─────────────────────────────────────────

# FE3 (Front End 3) — Microsoft's Windows Update delivery endpoint.
# The /secured variant accepts XBL3.0 authentication directly.
FE3_SECURED_URL = "https://fe3cr.delivery.mp.microsoft.com/ClientWebService/client.asmx/secured"

# Local install root for downloaded Win32 MS Store games.
MS_INSTALL_DIR  = os.path.expanduser("~/.local/share/unifideck/microsoft")
MS_MARKER_FILE  = ".unifideck-ms-id"

# Device-attribute string required by FE3 — presents as a Windows 10 Desktop client.
# Locale-sensitive fields (InstallLanguage, OSUILocale) are injected at call time
# via MicrosoftConnector._fe3_device_attrs().
_FE3_DEVICE_ATTRS_TEMPLATE = (
    "E:BranchReadinessLevel=CBB&ProcessorIdentifier=Intel64+Family+6+Model+142+Stepping+10&"
    "CurrentBranch=rs4_release&DataVer_RS5=1809&FlightRing=Retail&AttrDataVer=57&"
    "InstallLanguage={locale}&OSUILocale={locale}&InstallationType=Client&"
    "FlightingBranchName=&Version_RS5=10&UpgEx_RS5=Green&GStatus_RS5=2&OSSkuId=48&"
    "app=APPHOSTUI&ProcessorManufacturer=GenuineIntel&AppVer=10.0.17134.471&"
    "OSArchitecture=AMD64&UpdateManagementGroup=2&IsDeviceRetailDemo=0&"
    "IsFlightingEnabled=0&TelemetryLevel=1&DefaultUserRegion=244&"
    "OSVersion=10.0.17134.471&OSRollbackAllowed=0&DeviceFamily=Windows.Desktop"
)

# How old (seconds) an access token can be before we proactively refresh it.
TOKEN_REFRESH_THRESHOLD = 2400   # 40 min (MS tokens last ~60 min)

# Acquisition types that mean the user owns the title.
# "Free" / "FreeToPlay" cover F2P games the user has added to their library.
# Game Pass / subscription titles use "GamePass" or "Subscription" — intentionally excluded.
OWNED_TYPES = {"Purchase", "Owned", "Free", "FreeToPlay"}

# Product kinds that may contain launchable games.
# Case-insensitive comparison — see get_library() filter.
_GAME_KINDS = {"game", "durable", "application"}

# ───────────────────────── SSL helper ──────────────────────────────────────

def _ssl_ctx_strict() -> ssl.SSLContext:
    """SSL context for authentication and API endpoints.

    SteamOS ships an incomplete CA bundle that cannot verify login.live.com.
    We try certifi first (bundled with many Python installs); if unavailable
    we fall back to disabling certificate verification.  This is acceptable
    because we are exchanging tokens with a hard-coded Microsoft endpoint —
    the URL itself is not attacker-controlled.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass

    # certifi not available — fall back to no verification
    import logging as _log
    _log.getLogger(__name__).warning(
        "[MS] certifi not found — disabling SSL verification for MS auth endpoints. "
        "Install certifi for proper certificate validation."
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def _ssl_ctx() -> ssl.SSLContext:
    """Permissive SSL context for CDN package downloads only.

    Microsoft delivery CDN URLs sometimes present certificates that the Steam Deck
    system CA bundle cannot verify.  This context is intentionally restricted to
    unauthenticated binary downloads — never used for OAuth or token exchanges.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http_post(url: str, data: dict, headers: dict) -> dict:
    """Synchronous HTTP POST returning parsed JSON."""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx_strict()) as r:
        return json.loads(r.read().decode())


def _http_post_json(url: str, payload: dict, headers: dict) -> dict:
    """Synchronous HTTP POST with JSON body returning parsed JSON."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx_strict()) as r:
        return json.loads(r.read().decode())


def _http_get(url: str, headers: dict) -> dict:
    """Synchronous HTTP GET returning parsed JSON."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx_strict()) as r:
        return json.loads(r.read().decode())


# ──────────────────────────── connector ────────────────────────────────────

class MicrosoftConnector(Store):
    """
    Microsoft Store / Xbox Live library connector.

    Surfaces only *purchased* (non-Game Pass) titles that declare
    Windows.Desktop device family compatibility — the subset most likely
    to work (or be attempted) via Proton on SteamOS.

    Installation is NOT supported: Microsoft Store DRM packages cannot be
    unpacked and run on Linux.  The connector is intentionally read-only
    (library display + ProtonDB compatibility lookup).
    """

    def __init__(self, plugin_dir: Optional[str] = None, plugin_instance=None):
        self.plugin_dir      = plugin_dir
        self.plugin_instance = plugin_instance

        # In-memory token cache
        self._ms_access_token:  Optional[str] = None
        self._ms_refresh_token: Optional[str] = None
        self._token_saved_at:   float = 0.0

        # Cached XBL/XSTS tokens (short-lived, rebuilt per sync)
        self._xbl_token:  Optional[str] = None
        self._xsts_token: Optional[str] = None
        self._xsts_rp:    Optional[str] = None
        self._user_hash:  Optional[str] = None
        self._xuid:       Optional[str] = None

        self._load_tokens()
        # Per-game Win32 metadata cache populated by get_library / _scan_pc_games.
        # Schema: {product_id: {'is_win32': bool, 'wu_bundle_id': str, ...}}
        self._game_metadata: Dict[str, dict] = {}
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
        """Build the FE3 device-attribute string with the user's locale injected."""
        loc = self._get_locale()
        return _FE3_DEVICE_ATTRS_TEMPLATE.format(locale=loc)

    def _validated_install_dir(self, game_id: str) -> str:
        """
        Return the install directory path for *game_id* after verifying it
        cannot escape MS_INSTALL_DIR via path-traversal sequences.

        Raises ValueError if game_id produces a path outside MS_INSTALL_DIR.
        """
        install_dir = os.path.join(MS_INSTALL_DIR, game_id)
        # Resolve symlinks / '..' before comparison
        if not os.path.abspath(install_dir).startswith(
            os.path.abspath(MS_INSTALL_DIR) + os.sep
        ):
            raise ValueError(
                f"[MS] Refusing to use install path outside MS_INSTALL_DIR: {install_dir!r}"
            )
        return install_dir

    # ── Store interface ──────────────────────────────────────────────────

    @property
    def store_name(self) -> str:
        return "microsoft"

    async def is_available(self) -> bool:
        """Return True if we have a saved (and refreshable) token."""
        if not os.path.exists(TOKEN_FILE):
            return False
        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            # Only check for a refresh_token.  The scope is hard-coded in the
            # auth flow (MS_SCOPE) so a valid token file always implies the
            # correct scope.  Checking it here was fragile — _save_tokens
            # previously omitted the field, making every sync silently fail.
            return bool(data.get("refresh_token"))
        except Exception:
            return False

    async def start_auth(self) -> Dict[str, Any]:
        """Build the Microsoft OAuth URL and launch CDP monitoring."""
        # Clear Microsoft cookies from CEF so the user always sees the login form.
        # Without this, stale cookies cause Microsoft to silently SSO and emit
        # oauth20_desktop.srf?removed=true immediately — before the form appears.
        # We do NOT use prompt=login or prompt=select_account because those also
        # trigger removed=true (Microsoft tears down the session before re-issuing
        # the code, and CEF never follows that second redirect).
        # Strategy: clear cookies (forces fresh form) + no prompt (clean code flow).
        try:
            from ..auth.browser import CDPOAuthMonitor as _Mon
            _mon = _Mon()
            await _mon.clear_cookies_for_domain("login.live.com")
            await _mon.clear_cookies_for_domain("live.com")
            await _mon.clear_cookies_for_domain("microsoft.com")
            await _mon.clear_cookies_for_domain("login.microsoftonline.com")
            logger.info("[MS] Cleared Microsoft cookies before auth")
        except Exception as e:
            logger.debug(f"[MS] Cookie clear before auth (non-fatal): {e}")

        auth_url = (
            f"{MS_AUTH_URL}"
            f"?client_id={MS_CLIENT_ID}"
            f"&redirect_uri={urllib.parse.quote(MS_REDIRECT)}"
            f"&response_type=code"
            f"&scope={urllib.parse.quote(MS_SCOPE)}"
            # No prompt= — after cookie clearing Microsoft shows the login form
            # directly and issues ?code= on success with no removed=true detour.
        )

        # Store auth_url so the monitor can re-navigate if it hits removed=true.
        self._pending_auth_url = auth_url

        # Cancel any previous monitor task before starting a new one so that
        # double-clicking "Connect" doesn't spawn two competing monitors.
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
                lambda: _http_post(
                    MS_TOKEN_URL,
                    {
                        "client_id":    MS_CLIENT_ID,
                        "redirect_uri": MS_REDIRECT,
                        "code":         auth_code,
                        "grant_type":   "authorization_code",
                        "scope":        MS_SCOPE,
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
        """Clear stored tokens."""
        self._ms_access_token  = None
        self._ms_refresh_token = None
        self._xbl_token        = None
        self._xsts_token       = None
        self._xsts_rp          = None
        self._user_hash        = None
        self._xuid             = None
        try:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
        except Exception as e:
            logger.warning(f"[MS] Could not remove token file: {e}")

        # Clear browser cookies for Microsoft domains via CDP
        # Also close any lingering oauth20_desktop.srf redirect page so that
        # the stale one-time code it contains cannot be mistakenly replayed
        # when the user tries to reconnect.
        try:
            from ..auth.browser import CDPOAuthMonitor
            monitor = CDPOAuthMonitor()
            await monitor.clear_cookies_for_domain("login.live.com")
            await monitor.clear_cookies_for_domain("live.com")
            await monitor.clear_cookies_for_domain("microsoft.com")
            await monitor.clear_cookies_for_domain("login.microsoftonline.com")
        except Exception:
            pass

        return {"success": True, "message": "Logged out from Microsoft Store"}

    async def get_library(self) -> List[Game]:
        """
        Fetch the user's owned (purchased) PC-compatible games.

        Steps:
          1. Ensure we have a fresh MS access token
          2. Build XBL → XSTS token chain
          3. Query Collections API (owned purchases only)
          4. Batch-fetch product details to filter Windows.Desktop games
        """
        if not await self.is_available():
            # Provide an actionable diagnostic instead of a generic warning.
            if not os.path.exists(TOKEN_FILE):
                logger.error(
                    "[MS] Not authenticated — token file does not exist. "
                    "Authenticate via Quick Access Menu → Unifideck → Microsoft."
                )
            else:
                try:
                    with open(TOKEN_FILE) as f:
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
            await self._ensure_fresh_ms_token()

            # ── 2. XBL / XSTS token chain ────────────────────────────────
            ok = await asyncio.get_event_loop().run_in_executor(None, self._build_xbl_chain)
            if not ok:
                logger.warning(
                    "[MS] Could not build XBL/XSTS token chain — "
                    "proceeding with Bearer only library query"
                )

            # ── 3. Collections API ────────────────────────────────────────
            raw_items = await asyncio.get_event_loop().run_in_executor(
                None, self._query_collections
            )
            logger.info(f"[MS] Collections returned {len(raw_items)} raw items")

            # Log productKind distribution for diagnostics — helps identify
            # items dropped by the filter below.
            kind_counts: Dict[str, int] = {}
            acq_counts:  Dict[str, int] = {}
            for item in raw_items:
                k = item.get("productKind", "unknown")
                a = item.get("acquisitionType", "unknown")
                kind_counts[k] = kind_counts.get(k, 0) + 1
                acq_counts[a]  = acq_counts.get(a, 0) + 1
            logger.info(f"[MS] Collections productKind breakdown: {kind_counts}")
            logger.info(f"[MS] Collections acquisitionType breakdown: {acq_counts}")

            # Filter: owned titles only (purchased + F2P), exclude Game Pass / subscriptions
            purchased = [
                item for item in raw_items
                if item.get("acquisitionType") in OWNED_TYPES
                and (item.get("productKind") or "").lower() in _GAME_KINDS
            ]
            logger.info(f"[MS] {len(purchased)} owned games after Game Pass filter")

            if not purchased:
                return []

            # ── 4. Product detail + Win32/UWP classification ─────────────────
            product_ids = [item["productId"] for item in purchased if item.get("productId")]
            game_meta = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._scan_pc_games(product_ids)
            )
            win32_count = sum(1 for m in game_meta.values() if m.get("is_win32"))
            logger.info(
                f"[MS] {len(game_meta)} products classified "
                f"({win32_count} Win32 installable, {len(game_meta) - win32_count} UWP not compatible)"
            )

            # Cache Win32 metadata for install_game to use later
            self._game_metadata.update(
                {pid: m for pid, m in game_meta.items() if m.get("is_win32")}
            )

            # Detect already-installed games
            installed = self.get_installed()

            # Build Game objects for ALL owned titles.
            # UWP-only games get store_tags=["not_compatible"] so the frontend
            # can show an informational notice instead of an Install button.
            id_to_item = {item["productId"]: item for item in purchased if item.get("productId")}
            games: List[Game] = []
            for pid, meta in game_meta.items():
                item   = id_to_item.get(pid, {})
                title  = (
                    item.get("productTitle")
                    or item.get("displayCatalogItem", {})
                       .get("localizedProperties", [{}])[0]
                       .get("productTitle")
                    or meta.get("title")
                    or pid
                )
                inst_info    = installed.get(pid)
                is_installed = inst_info is not None

                # Build store_tags list
                tags: List[str] = []
                if not meta.get("is_win32"):
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

    def _load_tokens(self):
        try:
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE) as f:
                    data = json.load(f)
                self._ms_access_token  = data.get("access_token")
                self._ms_refresh_token = data.get("refresh_token")
                self._token_saved_at   = data.get("saved_at", 0.0)
                logger.info("[MS] Loaded tokens from disk")
        except Exception as e:
            logger.warning(f"[MS] Could not load tokens: {e}")

    def _save_tokens(self):
        try:
            os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
            # Write with mode 0o600 so only the owning user can read the tokens.
            fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(
                    {
                        "access_token":  self._ms_access_token,
                        "refresh_token": self._ms_refresh_token,
                        "saved_at":      self._token_saved_at,
                        "scope":         MS_SCOPE,
                    },
                    f,
                )
        except Exception as e:
            logger.warning(f"[MS] Could not save tokens: {e}")

    async def _ensure_fresh_ms_token(self):
        """Proactively refresh the MS access token if it is near expiry."""
        age = time.time() - self._token_saved_at
        if age < TOKEN_REFRESH_THRESHOLD:
            return

        if not self._ms_refresh_token:
            logger.warning("[MS] No refresh token available")
            return

        try:
            logger.info(f"[MS] Refreshing MS access token (age={age:.0f}s)")
            token_data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _http_post(
                    MS_TOKEN_URL,
                    {
                        "client_id":     MS_CLIENT_ID,
                        "redirect_uri":  MS_REDIRECT,
                        "refresh_token": self._ms_refresh_token,
                        "grant_type":    "refresh_token",
                        "scope":         MS_SCOPE,
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
            else:
                logger.warning(f"[MS] Token refresh failed: {token_data}")
        except Exception as e:
            logger.error(f"[MS] Token refresh error: {e}", exc_info=True)

    # ── XBL token chain (synchronous, run in executor) ───────────────────

    def _build_xbl_chain(self) -> bool:
        """
        Build XBL user token → XSTS token chain.
        Returns True on success, False on failure.
        """
        # Always reset the chain so a partial previous attempt cannot leave
        # an inconsistent state (xbl_token set but xsts_token None).
        self._xbl_token  = None
        self._xsts_token = None
        self._xsts_rp    = None
        self._user_hash  = None
        try:
            # Step A: XBL user token
            # Try multiple contract-version / prefix combos:
            #   v2 + t= : designed for OAuth2 JWT tokens (Xboxlive.signin scope)
            #   v1 + d= : legacy compact MSA tickets (MBI_SSL scope)
            _xbl_candidates = [
                ("2", f"t={self._ms_access_token}"),  # JWT / contract v2
                ("1", f"d={self._ms_access_token}"),  # compact MSA / contract v1
                ("1", f"t={self._ms_access_token}"),  # JWT / contract v1 fallback
            ]
            xbl_resp = None
            for _cv, _rps in _xbl_candidates:
                try:
                    xbl_resp = _http_post_json(
                        XBL_AUTH_URL,
                        {
                            "Properties": {
                                "AuthMethod": "RPS",
                                "SiteName":   "user.auth.xboxlive.com",
                                "RpsTicket":  _rps,
                            },
                            "RelyingParty": "http://auth.xboxlive.com",
                            "TokenType":    "JWT",
                        },
                        {
                            "Content-Type":           "application/json",
                            "Accept":                 "application/json",
                            "x-xbl-contract-version": _cv,
                            "User-Agent":             "XboxReplay; XboxLiveAuth/3.0",
                            "Accept-Language":        self._get_locale(),
                        },
                    )
                    if xbl_resp.get("Token"):
                        logger.info(
                            f"[MS] XBL auth OK (contract-v{_cv}, "
                            f"prefix={_rps[:2]!r})"
                        )
                        break
                    else:
                        logger.debug(
                            f"[MS] XBL no token (contract-v{_cv}, "
                            f"prefix={_rps[:2]!r}): {xbl_resp}"
                        )
                        xbl_resp = None
                except Exception as _xbl_err:
                    _body = ""
                    if hasattr(_xbl_err, "read"):
                        try:
                            _body = _xbl_err.read().decode("utf-8", errors="replace")
                        except Exception:
                            pass
                    logger.debug(
                        f"[MS] XBL failed (contract-v{_cv}, "
                        f"prefix={_rps[:2]!r}): {_xbl_err}"
                        f"{(' body=' + _body[:500]) if _body else ''}"
                    )
                    xbl_resp = None

            if xbl_resp is None or not xbl_resp.get("Token"):
                logger.error("[MS] XBL user token failed with all contract/prefix combos")
                return False

            self._xbl_token = xbl_resp.get("Token")
            if not self._xbl_token:
                logger.error(f"[MS] XBL token missing in response: {xbl_resp}")
                return False

            # Extract user hash (uhs) — needed for the Authorization header
            display_claims = xbl_resp.get("DisplayClaims", {})
            xui = display_claims.get("xui", [{}])
            self._user_hash = xui[0].get("uhs") if xui else None

            logger.info(f"[MS] ✓ XBL user token obtained (uhs={self._user_hash})")

            # Step B: XSTS token — try RP + SandboxId combinations in order.
            # https://licensing.xboxlive.com/ is required for the Collections API
            # but rejects SandboxId="RETAIL" on most accounts — use "" instead.
            _xsts_headers = {
                "Content-Type":           "application/json",
                "Accept":                 "application/json",
                "x-xbl-contract-version": "1",
                "User-Agent":             "XboxReplay; XboxLiveAuth/3.0",
                "Accept-Language":        self._get_locale(),
            }
            _xsts_candidates = [
                (XSTS_RP,                          ""),        # licensing + empty sandbox ← preferred
                (XSTS_RP,                          "RETAIL"),  # licensing + RETAIL
                ("http://licensing.xboxlive.com/", ""),        # legacy HTTP + empty sandbox
                ("http://xboxlive.com",            "RETAIL"),  # generic RP fallback
            ]
            xsts_resp = None
            _used_rp  = None
            for _rp, _sandbox in _xsts_candidates:
                try:
                    xsts_resp = _http_post_json(
                        XSTS_URL,
                        {
                            "Properties": {
                                "SandboxId":  _sandbox,
                                "UserTokens": [self._xbl_token],
                            },
                            "RelyingParty": _rp,
                            "TokenType":    "JWT",
                        },
                        _xsts_headers,
                    )
                    _used_rp      = _rp
                    self._xsts_rp = _rp
                    logger.info(f"[MS] ✓ XSTS obtained with RP={_rp!r} sandbox={_sandbox!r}")
                    break
                except Exception as _xsts_err:
                    _body = ""
                    if hasattr(_xsts_err, "read"):
                        try:
                            _body = _xsts_err.read().decode("utf-8", errors="replace")
                        except Exception:
                            pass
                    logger.warning(
                        f"[MS] XSTS failed (RP={_rp!r} sandbox={_sandbox!r}): "
                        f"{_xsts_err}{(' body=' + _body[:500]) if _body else ''}"
                    )
                    xsts_resp = None
            if xsts_resp is None:
                logger.error("[MS] XSTS failed with all RP/SandboxId combinations")
                return False

            # Error 2148916238 = account does not have an Xbox profile yet
            if "XErr" in xsts_resp:
                xerr = xsts_resp["XErr"]
                logger.error(f"[MS] XSTS error code: {xerr}")
                if xerr == 2148916238:
                    logger.error("[MS] Account has no Xbox profile — create one at xbox.com")
                elif xerr == 2148916233:
                    logger.error("[MS] Account is from a country where Xbox is not available")
                return False

            self._xsts_token = xsts_resp.get("Token")
            if not self._xsts_token:
                logger.error(f"[MS] XSTS token missing: {xsts_resp}")
                return False

            # Also grab the XUID for the collections query
            xsts_claims = xsts_resp.get("DisplayClaims", {}).get("xui", [{}])
            self._xuid = xsts_claims[0].get("xid") if xsts_claims else None

            logger.info(f"[MS] ✓ XSTS token obtained (xuid={self._xuid})")
            return True

        except Exception as e:
            logger.error(f"[MS] XBL chain error: {e}", exc_info=True)
            return False

    # ── Collections API (synchronous, run in executor) ────────────────────

    def _query_collections(self) -> List[Dict]:
        """
        Query the Microsoft Collections API and return raw item dicts.
        Handles pagination automatically.  Requires a valid XSTS token
        with the licensing relying party — returns an empty list with an
        explicit error log if the token chain is incomplete.
        """
        _LICENSING_RPS = {
            "https://licensing.xboxlive.com/",
            "http://licensing.xboxlive.com/",
        }
        if not self._xsts_token or not self._user_hash or self._xsts_rp not in _LICENSING_RPS:
            logger.error(
                "[MS] Cannot query Collections — XBL/XSTS unavailable or non-licensing RP "
                f"(xsts_token={'set' if self._xsts_token else 'None'}, "
                f"rp={self._xsts_rp!r}).  The OAuth scope ({MS_SCOPE!r}) does not grant "
                f"Bearer access to the Store Library API, so no fallback is possible. "
                f"Ensure the XBL → XSTS chain completes successfully."
            )
            return []

        auth_header = f"XBL3.0 x={self._user_hash};{self._xsts_token}"
        headers = {
            "Authorization":          auth_header,
            "Content-Type":           "application/json",
            "MS-CV":                  "unifideck.1",
            "Accept":                 "application/json",
        }

        all_items: List[Dict] = []
        continuation_token = None

        for page_num in range(20):   # safety cap: 20 pages × 200 = 4000 items
            payload: Dict[str, Any] = {
                "beneficiaries": [
                    {
                        "identityType":         "b2b",
                        "identityValue":        self._xuid or "0",
                        "localTicketReference": "1",
                    }
                ],
                "market":           self._get_market(),
                "productSkuIds":    [],
                "country":          self._get_market(),
                "pageSize":         200,
            }
            # Only include continuationToken when paginating — sending null on the
            # first request can cause the Collections API to return an error.
            if continuation_token:
                payload["continuationToken"] = continuation_token

            try:
                resp = _http_post_json(COLLECTIONS_URL, payload, headers)
            except Exception as e:
                logger.error(f"[MS] Collections query page {page_num} failed: {e}")
                break

            items = resp.get("items", [])
            all_items.extend(items)
            logger.info(f"[MS] Collections page {page_num}: {len(items)} items")

            continuation_token = resp.get("continuationToken")
            if not continuation_token:
                break

        return all_items

    # ── Product detail + PC filter (synchronous, run in executor) ─────────

    def _query_store_library_bearer(self) -> List[Dict]:
        """
        Fallback: query owned PC games via the Microsoft Store Library API
        using Bearer auth (MS access token) — no licensing XSTS required.
        Returns items in the same format as _query_collections.
        """
        if not self._ms_access_token:
            logger.error("[MS] No MS access token for store library query")
            return []

        all_items: List[Dict] = []
        skip_items = 0
        page_size  = 100

        for page_num in range(20):
            url = (
                "https://storeedgefd.dsx.mp.microsoft.com/v9.0/me/library"
                f"?market={self._get_market()}"
                f"&locale={self._get_locale()}"
                "&deviceFamily=windows.desktop"
                f"&$skip={skip_items}"
                f"&$top={page_size}"
            )
            headers = {
                "Authorization": f"Bearer {self._ms_access_token}",
                "Accept":        "application/json",
                "User-Agent":    "Microsoft.WindowsStore/11910.1002.5.0",
                "MS-CV":         "unifideck.store.1",
            }
            try:
                resp = _http_get(url, headers)
            except Exception as e:
                logger.error(f"[MS] Store library page {page_num} failed: {e}")
                break

            products = resp.get("productsList", [])
            if not products:
                break

            for p in products:
                pid = p.get("productId") or p.get("ProductId")
                if not pid:
                    continue
                kind = p.get("productType") or p.get("ProductType", "Game")
                all_items.append({
                    "productId":       pid,
                    "productTitle":    p.get("name") or p.get("title") or "",
                    "productKind":     kind,
                    "acquisitionType": "Purchase",
                })

            logger.info(f"[MS] Store library page {page_num}: {len(products)} items")

            if len(products) < page_size:
                break
            skip_items += page_size

        logger.info(f"[MS] Store library (Bearer): {len(all_items)} total items")
        return all_items

    def _scan_pc_games(self, product_ids: List[str]) -> Dict[str, dict]:
        """
        Batch-scan products and classify them as Win32 or UWP.

        All products are returned so the caller can decide what to show;
        use ``meta["is_win32"]`` to distinguish downloadable games from
        UWP-only titles that carry ``"not_compatible"`` in their tags.

        Returns:
            Dict mapping product_id → {
                'is_win32':         bool,
                'wu_bundle_id':     str,    # non-empty only for Win32
                'wu_category_id':   str,
                'title':            str,
                'is_play_anywhere': bool,
            }
        """
        if not product_ids:
            return {}

        result: Dict[str, dict] = {}
        batch_size = 20

        for i in range(0, len(product_ids), batch_size):
            batch   = product_ids[i: i + batch_size]
            big_ids = ",".join(batch)
            url     = f"{PRODUCT_URL}?bigIds={big_ids}&market={self._get_market()}&locale={self._get_locale()}"

            try:
                data     = _http_get(url, {"Accept": "application/json", "User-Agent": "Unifideck/1.0", "MS-CV": "unifideck.2"})
                products = data.get("Products", [])

                for product in products:
                    pid = product.get("ProductId", "")
                    if not pid:
                        continue

                    _is_win32, meta = self._classify_product(product)
                    result[pid] = meta  # include Win32 AND UWP

            except Exception as e:
                logger.warning(f"[MS] Product scan failed for batch {i // batch_size}: {e}")

        return result

    def _classify_product(self, product: dict) -> Tuple[bool, dict]:
        """
        Determine whether a product is Win32 (installable) or UWP (unusable on Linux).

        Win32 indicators (both required):
          • FulfillmentData.WuBundleId is non-empty  →  FE3 download available
          • FulfillmentData.PackageFamilyName is null/empty  →  not MSIX-packaged

        The ``is_play_anywhere`` flag is set when any SKU exposes
        ``Properties.IsXboxPlayAnywhere == True``.  It is stored in
        ``store_tags`` as ``'play_anywhere'`` for the frontend.

        Returns:
            (is_win32: bool, metadata_dict)
        """
        meta: dict = {
            "is_win32":         False,
            "wu_bundle_id":     "",
            "wu_category_id":   "",
            "title":            "",
            "is_play_anywhere": False,
        }

        # Try to extract a title from LocalizedProperties
        for sku_avail in product.get("DisplaySkuAvailabilities", []):
            loc_props = sku_avail.get("Sku", {}).get("LocalizedProperties", [])
            if loc_props:
                meta["title"] = loc_props[0].get("SkuTitle", "")
                break

        for sku_avail in product.get("DisplaySkuAvailabilities", []):
            sku   = sku_avail.get("Sku", {})
            props = sku.get("Properties", {})
            fd    = props.get("FulfillmentData", {}) or {}

            # Detect Xbox Play Anywhere on any SKU (set once, never cleared)
            if props.get("IsXboxPlayAnywhere"):
                meta["is_play_anywhere"] = True

            package_family  = fd.get("PackageFamilyName") or ""
            wu_bundle_id    = fd.get("WuBundleId")        or ""
            wu_category_id  = fd.get("WuCategoryId")      or ""

            # Must have a download bundle AND must NOT be MSIX-packaged
            if wu_bundle_id and not package_family:
                meta["is_win32"]       = True
                meta["wu_bundle_id"]   = wu_bundle_id
                meta["wu_category_id"] = wu_category_id
                # Continue iterating so IsXboxPlayAnywhere on a later SKU is still caught

        if meta["is_win32"]:
            return True, meta

        return False, meta

    # ── Installed-game tracking ──────────────────────────────────────────

    def get_installed(self) -> Dict[str, dict]:
        """
        Scan the MS install directory for installed Win32 games.

        Returns:
            Dict mapping game_id → {'install_path', 'executable', 'installed_at'}
        """
        installed: Dict[str, dict] = {}
        if not os.path.exists(MS_INSTALL_DIR):
            return installed

        try:
            for entry in os.listdir(MS_INSTALL_DIR):
                entry_path = os.path.join(MS_INSTALL_DIR, entry)
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
        """
        Download and install a Win32 Microsoft Store game via the FE3 delivery API.

        Flow:
          1. Look up cached Win32 metadata (populated by get_library).
          2. Refresh MS token + rebuild XSTS chain for FE3 authentication.
          3. Call FE3 GetExtendedUpdateInfo2 SOAP endpoint → package download URLs.
          4. Download packages → extract to MS_INSTALL_DIR/<game_id>/.
          5. Locate the main executable and write the .unifideck-ms-id marker.
        """
        if not await self.is_available():
            return {"success": False, "error": "Not authenticated with Microsoft"}

        meta = self._game_metadata.get(game_id)
        if not meta:
            # Refresh metadata for this single product
            meta = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._fetch_single_product_meta(game_id),
            )
            if meta:
                self._game_metadata[game_id] = meta

        if not meta or not meta.get("is_win32"):
            return {"success": False, "error": "Game is UWP-only and cannot be installed on Linux"}

        wu_bundle_id = meta.get("wu_bundle_id", "")
        if not wu_bundle_id:
            return {"success": False, "error": "No download bundle ID found for this game"}

        # Validate path before touching the filesystem (Fix: path traversal + orphan dir).
        try:
            install_dir = self._validated_install_dir(game_id)
        except ValueError as e:
            logger.error(str(e))
            return {"success": False, "error": "Invalid game identifier"}

        try:
            await self._ensure_fresh_ms_token()
            ok = await asyncio.get_event_loop().run_in_executor(None, self._build_xbl_chain)
            if not ok:
                return {"success": False, "error": "Failed to build Xbox authentication chain"}

            # Only create the directory once auth is confirmed — avoids orphan
            # directories if the token refresh or XSTS chain fails.
            os.makedirs(install_dir, exist_ok=True)

            if progress_callback:
                await progress_callback({"phase": "preparing", "phase_message": "Requesting download links…"})

            # Get download URLs from the FE3 delivery service
            urls = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._get_fe3_download_urls(wu_bundle_id)
            )
            if not urls:
                return {"success": False, "error": "Microsoft delivery service returned no download URLs"}

            logger.info(f"[MS] Got {len(urls)} download URL(s) for {game_id}")

            # Download → extract → locate exe
            exe_path = await self._download_and_install(urls, install_dir, game_id, progress_callback)
            if not exe_path:
                return {"success": False, "error": "Installation complete but no executable found — check logs"}

            # Write marker (equivalent to .unifideck-id for GOG)
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

    # ── FE3 download pipeline (synchronous helpers, run in executor) ─────

    def _fetch_single_product_meta(self, game_id: str) -> Optional[dict]:
        """Fetch and classify a single product from the catalog API."""
        try:
            url  = f"{PRODUCT_URL}?bigIds={game_id}&market={self._get_market()}&locale={self._get_locale()}"
            data = _http_get(url, {"Accept": "application/json", "User-Agent": "Unifideck/1.0", "MS-CV": "unifideck.3"})
            for product in data.get("Products", []):
                is_win32, meta = self._classify_product(product)
                if is_win32:
                    return meta
        except Exception as e:
            logger.error(f"[MS] Single product fetch failed for {game_id}: {e}")
        return None

    def _get_fe3_download_urls(self, wu_bundle_id: str) -> List[str]:
        """
        Call the FE3 GetExtendedUpdateInfo2 SOAP endpoint and return package download URLs.

        Authentication: XBL3.0 x=<user_hash>;<xsts_token> in a WS-Security header.
        The existing XSTS token (RP = licensing.xboxlive.com) is accepted by FE3.
        """
        if not self._xsts_token or not self._user_hash:
            raise RuntimeError("[MS] XSTS token not available for FE3")

        import uuid as _uuid
        from datetime import datetime, timezone, timedelta

        now     = datetime.now(timezone.utc)
        created = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        expires = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg_id  = str(_uuid.uuid4())

        from xml.sax.saxutils import escape as _xml_escape
        xuid_safe       = _xml_escape(str(self._xuid or "0"))
        user_hash_safe  = _xml_escape(str(self._user_hash or ""))
        xsts_token_safe = _xml_escape(str(self._xsts_token or ""))
        wu_bundle_safe  = _xml_escape(str(wu_bundle_id))

        soap = f"""<s:Envelope
    xmlns:s="http://www.w3.org/2003/05/soap-envelope"
    xmlns:a="http://www.w3.org/2005/08/addressing"
    xmlns:u="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
  <s:Header>
    <a:Action s:mustUnderstand="1">http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService/GetExtendedUpdateInfo2</a:Action>
    <a:MessageID>urn:uuid:{msg_id}</a:MessageID>
    <a:ReplyTo><a:Address>http://www.w3.org/2005/08/addressing/anonymous</a:Address></a:ReplyTo>
    <a:To s:mustUnderstand="1">{FE3_SECURED_URL}</a:To>
    <o:Security s:mustUnderstand="1"
        xmlns:o="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
      <u:Timestamp><u:Created>{created}</u:Created><u:Expires>{expires}</u:Expires></u:Timestamp>
      <o:UsernameToken>
        <o:Username>{xuid_safe}</o:Username>
        <o:Password Type="http://schemas.xmlsoap.org/ws/2005/05/identity/NoProofKey">XBL3.0 x={user_hash_safe};{xsts_token_safe}</o:Password>
      </o:UsernameToken>
    </o:Security>
  </s:Header>
  <s:Body>
    <GetExtendedUpdateInfo2 xmlns="http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService">
      <updateIDs>
        <UpdateIdentity>
          <UpdateID>{wu_bundle_safe}</UpdateID>
          <RevisionNumber>1</RevisionNumber>
        </UpdateIdentity>
      </updateIDs>
      <infoTypes>
        <XmlUpdateFragmentType>FileUrl</XmlUpdateFragmentType>
        <XmlUpdateFragmentType>FileDecryption</XmlUpdateFragmentType>
        <XmlUpdateFragmentType>Extended</XmlUpdateFragmentType>
      </infoTypes>
      <deviceAttributes>{self._fe3_device_attrs()}</deviceAttributes>
    </GetExtendedUpdateInfo2>
  </s:Body>
</s:Envelope>"""

        req = urllib.request.Request(
            FE3_SECURED_URL,
            data=soap.encode("utf-8"),
            headers={"Content-Type": "application/soap+xml; charset=UTF-8", "SOAPAction": ""},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx_strict()) as r:
            response = r.read().decode("utf-8")

        import re as _re
        # Pull all <Url> elements from the SOAP response
        raw_urls = _re.findall(r"<Url>([^<]+)</Url>", response)
        urls = [u.strip() for u in raw_urls if u.strip().startswith("http")]
        if not urls:
            logger.warning(f"[MS] FE3 response contained no URLs for {wu_bundle_id}")
            logger.debug(f"[MS] FE3 raw response (first 2000 chars): {response[:2000]}")
        return urls

    def _download_file(self, url: str, dest_path: str) -> bool:
        """Download a single package file. Returns True on success."""
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Microsoft-Delivery-Optimization/10.0"},
            )
            with urllib.request.urlopen(req, timeout=600, context=_ssl_ctx()) as resp:
                chunk_size = 1024 * 1024  # 1 MB
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
            size_mb = os.path.getsize(dest_path) / (1024 * 1024)
            logger.info(f"[MS] Downloaded {os.path.basename(dest_path)} ({size_mb:.1f} MB)")
            return True
        except Exception as e:
            logger.error(f"[MS] Download failed for {url}: {e}")
            return False

    def _extract_package(self, pkg_path: str, dest_dir: str, _depth: int = 0) -> bool:
        """
        Extract an .appx / .msix / bundle file into dest_dir.

        .appx/.msix files are ZIP archives.  Bundles contain inner .appx files.
        For Win32 games the outer layer typically contains a standard installer exe.

        _depth is an internal recursion counter — callers should not set it.
        """
        import zipfile
        import tempfile

        _MAX_DEPTH = 3
        if _depth > _MAX_DEPTH:
            logger.warning(f"[MS] _extract_package: max recursion depth ({_MAX_DEPTH}) reached, skipping {pkg_path}")
            return False

        real_dest = os.path.realpath(dest_dir)

        try:
            with zipfile.ZipFile(pkg_path, "r") as z:
                members = z.namelist()

                # Detect bundle (contains inner .appx/.msix files)
                inner_pkgs = [
                    m for m in members
                    if (m.endswith(".appx") or m.endswith(".msix"))
                    and not m.startswith("_")
                ]
                if inner_pkgs:
                    # Extract and recurse into each inner package
                    with tempfile.TemporaryDirectory() as tmp:
                        for inner in inner_pkgs:
                            z.extract(inner, tmp)
                            self._extract_package(
                                os.path.join(tmp, inner), dest_dir, _depth=_depth + 1
                            )
                else:
                    # Plain package — extract everything except AppX metadata,
                    # with zip-slip protection on each member path.
                    extract = [
                        m for m in members
                        if not m.startswith("AppxMetadata/")
                        and m not in ("[Content_Types].xml", "AppxBlockMap.xml")
                        and not m.endswith(".appxsym")
                    ]
                    for member in extract:
                        target = os.path.realpath(os.path.join(dest_dir, member))
                        if not target.startswith(real_dest + os.sep) and target != real_dest:
                            logger.warning(f"[MS] Zip-slip blocked: {member!r} → {target}")
                            continue
                        z.extract(member, dest_dir)
            return True
        except Exception as e:
            logger.error(f"[MS] Extraction failed for {pkg_path}: {e}")
            return False

    def _find_executable(self, install_dir: str) -> Optional[str]:
        """
        Locate the main game executable after extraction.

        Priority:
          1. Common top-level launcher names (case-insensitive).
          2. Largest .exe in the tree (typically the game binary).
        """
        PRIORITY_NAMES = {
            "game.exe", "launcher.exe", "start.exe", "run.exe", "play.exe",
            # "setup.exe" intentionally excluded — it is an installer, not a launcher.
        }
        exe_files = []
        for root, dirs, files in os.walk(install_dir):
            dirs[:] = [d for d in dirs if d not in ("AppxMetadata", "__MACOSX")]
            for fname in files:
                if fname.lower().endswith(".exe"):
                    full = os.path.join(root, fname)
                    exe_files.append((os.path.getsize(full), fname.lower(), full))

        if not exe_files:
            return None

        for _, name, path in exe_files:
            if name in PRIORITY_NAMES:
                return path

        exe_files.sort(key=lambda x: x[0], reverse=True)
        return exe_files[0][2]

    async def _download_and_install(
        self,
        urls: List[str],
        install_dir: str,
        game_id: str,
        progress_callback=None,
    ) -> Optional[str]:
        """
        Download all package URLs, extract them, and return the exe path.
        Downloads run sequentially (no parallel I/O) to respect server rate limits.
        """
        import tempfile
        import shutil

        tmp_dir = tempfile.mkdtemp(prefix=f"unifideck_ms_{game_id}_")
        try:
            downloaded: List[str] = []
            total = len(urls)

            for idx, url in enumerate(urls):
                # Sanitize the filename: decode percent-encoding, then strip any
                # directory component so the file always lands in tmp_dir.
                raw_name = urllib.parse.unquote(url.split("?")[0].split("/")[-1])
                filename = os.path.basename(raw_name) or f"package_{idx}.appx"
                dest     = os.path.join(tmp_dir, filename)

                if progress_callback:
                    pct = int((idx / total) * 70)
                    await progress_callback({
                        "phase":         "downloading",
                        "phase_message": f"Downloading package {idx + 1}/{total}…",
                        "progress_percent": pct,
                        "downloaded_bytes": 0,
                        "total_bytes":      0,
                    })

                ok = await asyncio.get_event_loop().run_in_executor(
                    None, lambda u=url, d=dest: self._download_file(u, d)
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
                    None, lambda p=pkg: self._extract_package(p, install_dir)
                )

            if progress_callback:
                await progress_callback({"phase": "verifying", "phase_message": "Locating executable…", "progress_percent": 95})

            return self._find_executable(install_dir)

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── CDP auto-auth monitor ─────────────────────────────────────────────


    async def _monitor_and_complete_auth(self):
        """Background task: intercept the OAuth redirect via Network events."""
        try:
            code = await self._intercept_oauth_code_via_network(timeout=300)
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

    async def _intercept_oauth_code_via_network(self, timeout: float = 300) -> Optional[str]:
        """
        Capture the OAuth code via Network.requestWillBeSent on the MS login popup.

        Key problem solved here: when the login page navigates (email → password
        → 2FA → ...), CEF creates a NEW /json target with a new webSocketDebuggerUrl
        but keeps the OLD WebSocket connection open indefinitely.  Our listener must
        detect when a newer MS target has appeared and switch to it, otherwise it
        stays attached to a dead target for the full timeout.

        Strategy:
          - Attach to the current MS login page
          - Every second, check /json for a NEWER MS target (higher timestamp / new
            ws_url not yet seen)
          - If found: break the inner loop, close current WS, reattach to new target
          - If the WS closes on its own: also rescan immediately
        """
        import time
        try:
            import websockets
        except ImportError:
            logger.warning("[MS-net] websockets not available")
            return None

        MS_LOGIN_PATTERNS = (
            "login.live.com",
            "login.microsoftonline.com",
            "account.microsoft.com",
        )

        deadline = time.time() + timeout
        seen_ws: set = set()
        current_ws_url = None
        self._removed_count = 0  # reset counter for this auth attempt

        def scan_pages():
            """Return list of (url, ws_url) for unseen MS login pages."""
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8080/json", timeout=2
                ) as r:
                    pages = json.loads(r.read().decode())
                result = []
                for page in pages:
                    url    = page.get("url", "")
                    ws_url = page.get("webSocketDebuggerUrl", "")
                    if not ws_url or ws_url in seen_ws:
                        continue
                    if "removed=true" in url:
                        seen_ws.add(ws_url)   # mark seen, skip
                        continue
                    # Skip stale oauth20_desktop.srf?code= pages left open from a
                    # previous session — the code is already expired and attaching
                    # to that page would waste time before switching to the new one.
                    if "oauth20_desktop.srf" in url and "code=" in url:
                        seen_ws.add(ws_url)
                        continue
                    if any(p in url for p in MS_LOGIN_PATTERNS):
                        result.append((url, ws_url))
                return result
            except Exception as e:
                logger.debug(f"[MS-net] scan error: {e}")
                return []

        logger.info("[MS-net] Starting Network interception with live target tracking")

        # Wait for first MS page to appear
        while time.time() < deadline:
            pages = scan_pages()
            if pages:
                break
            await asyncio.sleep(0.3)

        while time.time() < deadline:
            pages = scan_pages()
            if not pages:
                await asyncio.sleep(0.3)
                continue

            # Take the latest unseen page
            page_url, ws_url = pages[-1]
            seen_ws.add(ws_url)
            current_ws_url = ws_url
            remaining = deadline - time.time()
            logger.info(f"[MS-net] Attaching to: {page_url[:80]} ({remaining:.0f}s left)")

            try:
                async with websockets.connect(
                    ws_url, ping_interval=None, open_timeout=10
                ) as ws:
                    msg_id = 1

                    async def send_cmd(method, params=None):
                        nonlocal msg_id
                        await ws.send(json.dumps(
                            {"id": msg_id, "method": method, "params": params or {}}
                        ))
                        msg_id += 1

                    await send_cmd("Network.enable", {})
                    await asyncio.wait_for(ws.recv(), timeout=10)
                    logger.info("[MS-net] Network.enable OK")

                    last_scan = time.time()

                    while time.time() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            # Every second: check if a newer target appeared
                            if time.time() - last_scan >= 1.0:
                                last_scan = time.time()
                                newer = scan_pages()
                                if newer:
                                    logger.info(
                                        f"[MS-net] Newer target detected: "
                                        f"{newer[-1][0][:60]} — switching"
                                    )
                                    break   # break inner loop → reattach
                            continue

                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        if msg.get("method") != "Network.requestWillBeSent":
                            continue

                        req_url = (
                            msg.get("params", {})
                               .get("request", {})
                               .get("url", "")
                        )
                        if "oauth20_desktop.srf" not in req_url:
                            continue

                        logger.info(f"[MS-net] requestWillBeSent → {req_url[:120]}")

                        if "code=" in req_url:
                            from urllib.parse import urlparse, parse_qs as _pqs
                            params = _pqs(urlparse(req_url).query)
                            code = params.get("code", [None])[0]
                            if code:
                                logger.info("[MS-net] ✓ OAuth code captured")
                                return code

                        elif "removed=true" in req_url:
                            _removed_count = getattr(self, '_removed_count', 0) + 1
                            self._removed_count = _removed_count
                            logger.warning(
                                f"[MS-net] removed=true (attempt {_removed_count}) — "
                                "clearing cookies and re-navigating"
                            )
                            if _removed_count > 3:
                                logger.error(
                                    "[MS-net] removed=true persists after 3 attempts — "
                                    "giving up"
                                )
                                return None
                            if self._pending_auth_url:
                                try:
                                    # Clear all Microsoft cookies on this page first
                                    await send_cmd("Network.enable", {})
                                    await send_cmd(
                                        "Network.clearBrowserCookies", {}
                                    )
                                    logger.info("[MS-net] Cookies cleared")
                                    await asyncio.sleep(0.3)
                                    await send_cmd(
                                        "Page.navigate",
                                        {"url": self._pending_auth_url}
                                    )
                                    logger.info("[MS-net] Re-navigated to auth URL")
                                    # Stay on this WS and wait for code=
                                except Exception as _nav_err:
                                    logger.debug(
                                        f"[MS-net] Re-navigation failed: {_nav_err}"
                                    )
                                    break
                            else:
                                break

            except websockets.exceptions.ConnectionClosed:
                logger.info("[MS-net] WS closed — rescanning")
            except Exception as e:
                logger.debug(f"[MS-net] Listener error: {e}")

            await asyncio.sleep(0.3)

        logger.warning("[MS-net] Timed out waiting for OAuth code")
        return None
