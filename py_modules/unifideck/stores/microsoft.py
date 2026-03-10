"""
Microsoft Store connector for Unifideck.

Authenticates via Microsoft OAuth + Xbox Live token chain, then queries
the Microsoft Collections API to list owned (purchased) games — excluding
Game Pass titles — that have a Windows PC (Desktop) release and are
therefore potential candidates for Proton/SteamOS compatibility.

Auth flow:
  1. Microsoft OAuth (live.com) → access_token + refresh_token
  2. XBL user token       (user.auth.xboxlive.com)
  3. XSTS token           (xsts.auth.xboxlive.com, RP = licensing.xboxlive.com)
  4. Collections query    (collections.mp.microsoft.com)
  5. Product details      (store.mp.microsoft.com) – PC-device-family filter

Note: Microsoft Store UWP games generally cannot be run on Linux/Proton.
      Only games with a Win32 / Windows.Desktop release are surfaced here.
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
MS_AUTH_URL    = "https://login.live.com/oauth20_authorize.srf"
MS_TOKEN_URL   = "https://login.live.com/oauth20_token.srf"
MS_SCOPE       = "Xboxlive.signin Xboxlive.offline_access"

XBL_AUTH_URL   = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL       = "https://xsts.auth.xboxlive.com/xsts/authorize"
XSTS_RP        = "https://licensing.xboxlive.com/"   # relying party for Collections

COLLECTIONS_URL = "https://collections.mp.microsoft.com/v8.0/collections/query"
PRODUCT_URL     = "https://store.mp.microsoft.com/v8.0/sdk/products"

TOKEN_FILE = os.path.expanduser("~/.config/unifideck/microsoft_token.json")

# How old (seconds) an access token can be before we proactively refresh it.
TOKEN_REFRESH_THRESHOLD = 2400   # 40 min (MS tokens last ~60 min)

# Acquisition types that mean the user *purchased* the title
PURCHASE_TYPES = {"Purchase", "Owned"}

# ───────────────────────── SSL helper ──────────────────────────────────────

def _ssl_ctx() -> ssl.SSLContext:
    """Permissive SSL context for Steam Deck compatibility."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _http_post(url: str, data: dict, headers: dict) -> dict:
    """Synchronous HTTP POST returning parsed JSON."""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as r:
        return json.loads(r.read().decode())


def _http_post_json(url: str, payload: dict, headers: dict) -> dict:
    """Synchronous HTTP POST with JSON body returning parsed JSON."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx()) as r:
        return json.loads(r.read().decode())


def _http_get(url: str, headers: dict) -> dict:
    """Synchronous HTTP GET returning parsed JSON."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as r:
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
        self._user_hash:  Optional[str] = None
        self._xuid:       Optional[str] = None

        self._load_tokens()
        logger.info("[MS] MicrosoftConnector initialised")

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
            return bool(data.get("refresh_token"))
        except Exception:
            return False

    async def start_auth(self) -> Dict[str, Any]:
        """Build the Microsoft OAuth URL and launch CDP monitoring."""
        auth_url = (
            f"{MS_AUTH_URL}"
            f"?client_id={MS_CLIENT_ID}"
            f"&redirect_uri={urllib.parse.quote(MS_REDIRECT)}"
            f"&response_type=code"
            f"&scope={urllib.parse.quote(MS_SCOPE)}"
            f"&display=touch"   # Mobile-friendly layout, easier in the Deck browser
        )

        # Background CDP monitor auto-completes auth when the browser redirects
        asyncio.create_task(self._monitor_and_complete_auth())

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
        self._user_hash        = None
        self._xuid             = None
        try:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
        except Exception as e:
            logger.warning(f"[MS] Could not remove token file: {e}")

        # Clear browser cookies for Microsoft domains via CDP
        try:
            from ..auth.browser import CDPOAuthMonitor
            monitor = CDPOAuthMonitor()
            await monitor.clear_cookies_for_domain("login.live.com")
            await monitor.clear_cookies_for_domain("live.com")
            await monitor.clear_cookies_for_domain("microsoft.com")
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
            logger.warning("[MS] Not authenticated — skipping library fetch")
            return []

        try:
            # ── 1. Refresh MS access token if stale ──────────────────────
            await self._ensure_fresh_ms_token()

            # ── 2. XBL / XSTS token chain ────────────────────────────────
            ok = await asyncio.get_event_loop().run_in_executor(None, self._build_xbl_chain)
            if not ok:
                logger.error("[MS] Could not build XBL/XSTS token chain")
                return []

            # ── 3. Collections API ────────────────────────────────────────
            raw_items = await asyncio.get_event_loop().run_in_executor(
                None, self._query_collections
            )
            logger.info(f"[MS] Collections returned {len(raw_items)} raw items")

            # Filter: purchased only, exclude bundles/add-ons
            purchased = [
                item for item in raw_items
                if item.get("acquisitionType") in PURCHASE_TYPES
                and item.get("productKind") in ("Game", "game")
            ]
            logger.info(f"[MS] {len(purchased)} purchased games after Game Pass filter")

            if not purchased:
                return []

            # ── 4. Product detail + PC device-family filter ───────────────
            product_ids = [item["productId"] for item in purchased if item.get("productId")]
            pc_product_ids = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._filter_pc_games(product_ids)
            )
            logger.info(f"[MS] {len(pc_product_ids)} games have Windows.Desktop support")

            # Build Game objects
            id_to_item = {item["productId"]: item for item in purchased if item.get("productId")}
            games: List[Game] = []
            for pid in pc_product_ids:
                item = id_to_item.get(pid, {})
                title = item.get("productTitle") or item.get("displayCatalogItem", {}).get("localizedProperties", [{}])[0].get("productTitle") or pid
                game = Game(
                    id=pid,
                    title=title,
                    store="microsoft",
                    is_installed=False,   # No installation support
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
            with open(TOKEN_FILE, "w") as f:
                json.dump(
                    {
                        "access_token":  self._ms_access_token,
                        "refresh_token": self._ms_refresh_token,
                        "saved_at":      self._token_saved_at,
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
        try:
            # Step A: XBL user token
            xbl_resp = _http_post_json(
                XBL_AUTH_URL,
                {
                    "Properties": {
                        "AuthMethod": "RPS",
                        "SiteName":   "user.auth.xboxlive.com",
                        "RpsTicket":  f"d={self._ms_access_token}",
                    },
                    "RelyingParty": "http://auth.xboxlive.com",
                    "TokenType":    "JWT",
                },
                {
                    "Content-Type": "application/json",
                    "Accept":       "application/json",
                    "x-xbl-contract-version": "1",
                },
            )
            self._xbl_token = xbl_resp.get("Token")
            if not self._xbl_token:
                logger.error(f"[MS] XBL token missing in response: {xbl_resp}")
                return False

            # Extract user hash (uhs) — needed for the Authorization header
            display_claims = xbl_resp.get("DisplayClaims", {})
            xui = display_claims.get("xui", [{}])
            self._user_hash = xui[0].get("uhs") if xui else None

            logger.info(f"[MS] ✓ XBL user token obtained (uhs={self._user_hash})")

            # Step B: XSTS token with licensing relying party
            xsts_resp = _http_post_json(
                XSTS_URL,
                {
                    "Properties": {
                        "SandboxId":  "RETAIL",
                        "UserTokens": [self._xbl_token],
                    },
                    "RelyingParty": XSTS_RP,
                    "TokenType":    "JWT",
                },
                {
                    "Content-Type": "application/json",
                    "Accept":       "application/json",
                    "x-xbl-contract-version": "1",
                },
            )

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
        Handles pagination automatically.
        """
        if not self._xsts_token or not self._user_hash:
            logger.error("[MS] XSTS token or user hash missing")
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
                        "identitytype":         "b2b",
                        "identityValue":        self._xuid or "0",
                        "localTicketReference": "1",
                    }
                ],
                "market":           "US",
                "productSkuIds":    [],
                "country":          "US",
                "entitlementFilters": ["Game"],
                "pageSize":         200,
                "continuationToken": continuation_token,
            }

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

    def _filter_pc_games(self, product_ids: List[str]) -> List[str]:
        """
        Batch-check product catalog for Windows.Desktop device family.
        Returns the list of product IDs that have a PC release.
        """
        if not product_ids:
            return []

        pc_ids: List[str] = []
        # The store API accepts up to ~20 IDs per request
        batch_size = 20

        for i in range(0, len(product_ids), batch_size):
            batch = product_ids[i : i + batch_size]
            big_ids = ",".join(batch)
            url = f"{PRODUCT_URL}?bigIds={big_ids}&market=US&locale=en-US"

            try:
                data = _http_get(
                    url,
                    {
                        "Accept":     "application/json",
                        "User-Agent": "Unifideck/1.0",
                        "MS-CV":      "unifideck.2",
                    },
                )
                products = data.get("Products", [])

                for product in products:
                    pid = product.get("ProductId", "")
                    if not pid:
                        continue

                    # Check all SKUs for Windows.Desktop support
                    has_pc = False
                    for sku in product.get("DisplaySkuAvailabilities", []):
                        for avail in sku.get("Availabilities", []):
                            conditions = avail.get("Conditions", {})
                            device_families = conditions.get("ClientConditions", {}).get(
                                "AllowedPlatforms", []
                            )
                            for df in device_families:
                                if "Windows.Desktop" in df.get("PlatformName", ""):
                                    has_pc = True
                                    break
                            if has_pc:
                                break
                        if has_pc:
                            break

                    # Fallback: check top-level Properties.PackageFamilyName or Platforms list
                    if not has_pc:
                        props = product.get("Properties", {})
                        platforms = props.get("Platforms", [])
                        for p in platforms:
                            if "Windows.Desktop" in p or "PC" in p:
                                has_pc = True
                                break

                    if has_pc:
                        pc_ids.append(pid)

            except Exception as e:
                logger.warning(f"[MS] Product detail fetch failed for batch {i//batch_size}: {e}")
                # On error, include all IDs from this batch (better to show too many)
                pc_ids.extend(batch)

        return pc_ids

    # ── CDP auto-auth monitor ─────────────────────────────────────────────

    async def _monitor_and_complete_auth(self):
        """Background task: detect Microsoft OAuth redirect in the browser and auto-complete."""
        try:
            from ..auth.browser import CDPOAuthMonitor
            monitor = CDPOAuthMonitor()
            code, store = await monitor.monitor_for_oauth_code(expected_store="microsoft", timeout=300)

            if code and store == "microsoft":
                logger.info("[MS] ✓ Auto-captured Microsoft auth code via CDP")
                result = await self.complete_auth(code)
                if result["success"]:
                    logger.info("[MS] ✓ Authentication completed automatically")
                else:
                    logger.error(f"[MS] Auto-auth failed: {result.get('error')}")
            else:
                logger.warning("[MS] CDP monitor timed out without capturing a Microsoft code")
        except Exception as e:
            logger.error(f"[MS] CDP monitor error: {e}", exc_info=True)
