"""
Ubisoft Connect API Client -- Authentication, Token Management, GraphQL

Handles direct REST API login with email/password + 2FA,
token refresh, and GraphQL library queries.

No CLI tool needed -- all communication is via HTTP(S).
"""
import asyncio
import base64
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

CLUB_APPID = "82b650c0-6cb3-40c0-9f41-25a53b62b206"
CLUB_GENOME_ID = "42d07c95-9914-4450-8b38-267c4e462b21"
CHROME_USERAGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/72.0.3626.121 Safari/537.36"
)

AUTH_URL = "https://public-ubiservices.ubi.com/v3/profiles/sessions"
GRAPHQL_URL = "https://public-ubiservices.ubi.com/v1/profiles/me/uplay/graphql"

# Token storage path
DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
TOKEN_FILE = os.path.join(DATA_DIR, "ubisoft_token.json")

# GraphQL query for fetching owned games.
# The Ubisoft API migrated away from `games(filterBy: { isOwned: true })`
# with `ownedPlatformGroups`.  The current schema exposes the user's
# library through `viewer { games { ... } }` with `limit`/`offset`
# pagination, and no longer provides per-game platform information.
LIBRARY_QUERY = """
query OwnedGames($limit: Int, $offset: Int) {
  viewer {
    games(limit: $limit, offset: $offset) {
      totalCount
      nodes {
        spaceId
        name
        coverUrl
        backgroundUrl
        bannerUrl
        releaseDate
      }
    }
  }
}
"""


class UbisoftAPIClient:
    """
    Ubisoft Connect REST/GraphQL API client.

    Handles email/password authentication, 2FA, token storage/refresh,
    and GraphQL library queries. No CLI tool or browser popup needed.
    """

    def __init__(self):
        self.tokens: Optional[Dict[str, Any]] = None
        self._load_tokens()

    # ========================================================================
    # Token Persistence
    # ========================================================================

    def _load_tokens(self) -> None:
        """Load saved tokens from disk."""
        try:
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, "r") as f:
                    self.tokens = json.load(f)
                logger.info("[Ubisoft API] Loaded saved tokens")
            else:
                self.tokens = None
        except Exception as e:
            logger.warning(f"[Ubisoft API] Failed to load tokens: {e}")
            self.tokens = None

    def _save_tokens(self, tokens: Dict[str, Any]) -> None:
        """Save tokens to disk atomically (write to tmp + rename)."""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp_path = TOKEN_FILE + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(tokens, f, indent=2)
            os.replace(tmp_path, TOKEN_FILE)
            self.tokens = tokens
            logger.info("[Ubisoft API] Tokens saved to disk")
        except Exception as e:
            logger.error(f"[Ubisoft API] Failed to save tokens: {e}")

    def _clear_tokens(self) -> None:
        """Remove tokens from memory and disk."""
        self.tokens = None
        try:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            logger.info("[Ubisoft API] Tokens cleared")
        except Exception as e:
            logger.warning(f"[Ubisoft API] Failed to delete token file: {e}")

    def has_tokens(self) -> bool:
        """Check if tokens are available in memory."""
        return (
            self.tokens is not None
            and "ticket" in self.tokens
            and "userId" in self.tokens
        )

    # ========================================================================
    # HTTP Helpers
    # ========================================================================

    def _base_headers(self) -> Dict[str, str]:
        """Common headers for all Ubisoft API calls."""
        return {
            "Ubi-AppId": CLUB_APPID,
            "Content-Type": "application/json",
            "User-Agent": CHROME_USERAGENT,
        }

    async def _create_session(self) -> aiohttp.ClientSession:
        """Create an aiohttp session with SSL verification disabled (Steam Deck compat)."""
        connector = aiohttp.TCPConnector(ssl=False)
        return aiohttp.ClientSession(connector=connector)

    # ========================================================================
    # Authentication -- Email/Password Login
    # ========================================================================

    async def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Authenticate with Ubisoft using email/password via Basic auth.

        Ubisoft's newer API requires a two-step "Ubi-Challenge" device-integrity
        handshake before credentials are accepted:
          1. Send credentials → server responds with a Ubi-Challenge response header
          2. Echo that header value back as a Ubi-Challenge request header on retry
          3. Server then returns either a session ticket or a 2FA challenge

        Returns:
            On success: {"success": True, "username": "..."}
            On 2FA required: {"success": True, "requires_2fa": True, "2fa_ticket": "...", "2fa_method": "..."}
            On failure: {"success": False, "error": "..."}
        """
        try:
            credentials = base64.b64encode(f"{email}:{password}".encode()).decode()
            headers = self._base_headers()
            headers["Authorization"] = f"Basic {credentials}"

            session = await self._create_session()
            try:
                # Step 1: initial request — may receive a Ubi-Challenge nonce
                async with session.post(
                    AUTH_URL,
                    headers=headers,
                    json={"rememberMe": True},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    status = resp.status
                    body = await resp.json()
                    ubi_challenge = resp.headers.get("Ubi-Challenge")

                logger.info(
                    f"[Ubisoft API] Step 1 response: HTTP {status}, "
                    f"has_ticket={'ticket' in body}, "
                    f"has_2fa={'twoFactorAuthenticationTicket' in body}, "
                    f"has_challenge={ubi_challenge is not None}"
                )

                # Check for 2FA BEFORE retrying with Ubi-Challenge.
                # Ubisoft may return a 2FA challenge on the FIRST request;
                # if we blindly retry with the challenge header, the server
                # may issue a full session ticket and skip 2FA entirely.
                if "twoFactorAuthenticationTicket" in body:
                    two_fa_ticket = body["twoFactorAuthenticationTicket"]
                    two_fa_method = body.get("twoFactorAuthenticationMethod", "unknown")
                    logger.info(
                        f"[Ubisoft API] 2FA required (method: {two_fa_method}) — "
                        f"detected before Ubi-Challenge retry"
                    )
                    return {
                        "success": True,
                        "requires_2fa": True,
                        "2fa_ticket": two_fa_ticket,
                        "2fa_method": two_fa_method,
                    }

                # Step 2: if the server issued a Ubi-Challenge, echo it back.
                # Only retry if Step 1 did NOT already return a 2FA challenge.
                # This is a device-integrity check (anti-bot nonce), not 2FA.
                if ubi_challenge:
                    logger.info(
                        "[Ubisoft API] Ubi-Challenge received — retrying with challenge header"
                    )
                    headers["Ubi-Challenge"] = ubi_challenge
                    async with session.post(
                        AUTH_URL,
                        headers=headers,
                        json={"rememberMe": True},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp2:
                        status = resp2.status
                        body = await resp2.json()

                    logger.info(
                        f"[Ubisoft API] Step 2 (post-challenge) response: HTTP {status}, "
                        f"has_ticket={'ticket' in body}, "
                        f"has_2fa={'twoFactorAuthenticationTicket' in body}"
                    )

                if status == 401:
                    logger.warning("[Ubisoft API] Login failed: invalid credentials")
                    return {"success": False, "error": "Invalid email or password"}

                if status != 200:
                    error_msg = body.get("message", f"HTTP {status}")
                    logger.warning(f"[Ubisoft API] Login failed: {error_msg}")
                    return {"success": False, "error": error_msg}

                # Check for 2FA requirement again (post Ubi-Challenge retry)
                if "twoFactorAuthenticationTicket" in body:
                    two_fa_ticket = body["twoFactorAuthenticationTicket"]
                    two_fa_method = body.get("twoFactorAuthenticationMethod", "unknown")
                    logger.info(
                        f"[Ubisoft API] 2FA required (method: {two_fa_method})"
                    )
                    return {
                        "success": True,
                        "requires_2fa": True,
                        "2fa_ticket": two_fa_ticket,
                        "2fa_method": two_fa_method,
                    }

                # Successful login (only if no 2FA ticket — defensive guard)
                if "ticket" in body and "twoFactorAuthenticationTicket" not in body:
                    tokens = self._extract_tokens(body, email)
                    self._save_tokens(tokens)
                    logger.info(
                        f"[Ubisoft API] Login successful for {tokens.get('username', email)}"
                    )
                    return {
                        "success": True,
                        "username": tokens.get("username", email),
                    }

                logger.error("[Ubisoft API] Login: unexpected response shape")
                return {"success": False, "error": "Unexpected response from Ubisoft"}
            finally:
                await session.close()

        except asyncio.TimeoutError:
            logger.error("[Ubisoft API] Login timed out")
            return {"success": False, "error": "Connection timed out"}
        except Exception as e:
            logger.exception(f"[Ubisoft API] Login error: {e}")
            return {"success": False, "error": str(e)}

    # ========================================================================
    # Authentication -- 2FA Completion
    # ========================================================================

    async def complete_2fa(self, code: str, two_fa_ticket: str) -> Dict[str, Any]:
        """
        Complete 2FA login with verification code.

        Args:
            code: 6-digit 2FA code (from email, authenticator app, etc.)
            two_fa_ticket: The twoFactorAuthenticationTicket from login response

        Returns:
            On success: {"success": True, "username": "..."}
            On failure: {"success": False, "error": "..."}
        """
        try:
            headers = self._base_headers()
            headers["Authorization"] = f"ubi_2fa_v1 t={two_fa_ticket}"
            headers["Ubi-2FACode"] = code

            session = await self._create_session()
            try:
                async with session.post(
                    AUTH_URL,
                    headers=headers,
                    json={"rememberMe": True},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    status = resp.status
                    body = await resp.json()

                    if status != 200 or "ticket" not in body:
                        error_msg = body.get("message", f"2FA verification failed (HTTP {status})")
                        logger.warning(f"[Ubisoft API] 2FA failed: {error_msg}")
                        return {"success": False, "error": error_msg}

                    tokens = self._extract_tokens(body)
                    self._save_tokens(tokens)
                    logger.info(f"[Ubisoft API] 2FA login successful for {tokens.get('username', 'user')}")
                    return {
                        "success": True,
                        "username": tokens.get("username", ""),
                    }
            finally:
                await session.close()

        except asyncio.TimeoutError:
            logger.error("[Ubisoft API] 2FA timed out")
            return {"success": False, "error": "Connection timed out"}
        except Exception as e:
            logger.exception(f"[Ubisoft API] 2FA error: {e}")
            return {"success": False, "error": str(e)}

    # ========================================================================
    # Token Refresh
    # ========================================================================

    async def refresh_token(self) -> bool:
        """
        Refresh the auth ticket before it expires.

        Primary: PUT /v3/profiles/sessions with Ubi_v1 auth
        Fallback: POST with rememberMeTicket (if available)

        Returns:
            True if refresh succeeded, False otherwise.
        """
        if not self.tokens:
            return False

        # Primary refresh: PUT with current ticket
        ticket = self.tokens.get("ticket", "")
        session_id = self.tokens.get("sessionId", "")

        if ticket:
            try:
                headers = self._base_headers()
                headers["Authorization"] = f"Ubi_v1 t={ticket}"
                if session_id:
                    headers["Ubi-SessionId"] = session_id

                session = await self._create_session()
                try:
                    async with session.put(
                        AUTH_URL,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 200:
                            body = await resp.json()
                            if "ticket" in body:
                                tokens = self._extract_tokens(body)
                                # Preserve userId/username from original tokens
                                tokens["userId"] = tokens.get("userId") or self.tokens.get("userId", "")
                                tokens["username"] = tokens.get("username") or self.tokens.get("username", "")
                                self._save_tokens(tokens)
                                logger.info("[Ubisoft API] Token refreshed via PUT")
                                return True
                        else:
                            logger.warning(
                                f"[Ubisoft API] PUT refresh returned HTTP {resp.status}"
                            )
                finally:
                    await session.close()
            except Exception as e:
                logger.warning(f"[Ubisoft API] PUT refresh failed: {e}")

        # Fallback: POST with rememberMeTicket
        remember_ticket = self.tokens.get("rememberMeTicket", "")
        if remember_ticket:
            try:
                headers = self._base_headers()
                headers["Authorization"] = f"rm_v1 t={remember_ticket}"

                session = await self._create_session()
                try:
                    async with session.post(
                        AUTH_URL,
                        headers=headers,
                        json={"rememberMe": True},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 200:
                            body = await resp.json()
                            if "ticket" in body:
                                tokens = self._extract_tokens(body)
                                tokens["userId"] = tokens.get("userId") or self.tokens.get("userId", "")
                                tokens["username"] = tokens.get("username") or self.tokens.get("username", "")
                                self._save_tokens(tokens)
                                logger.info("[Ubisoft API] Token refreshed via rememberMe")
                                return True
                finally:
                    await session.close()
            except Exception as e:
                logger.warning(f"[Ubisoft API] rememberMe refresh failed: {e}")

        # Both refresh paths failed -- clear tokens
        logger.error("[Ubisoft API] All refresh attempts failed, clearing tokens")
        self._clear_tokens()
        return False

    async def ensure_valid_token(self) -> bool:
        """
        Check if token needs refresh and refresh if necessary.

        Returns:
            True if token is valid (or was successfully refreshed), False otherwise.
        """
        if not self.tokens:
            return False

        refresh_time = self.tokens.get("refreshTime", 0)
        if time.time() > refresh_time:
            logger.info("[Ubisoft API] Token expired, attempting refresh")
            return await self.refresh_token()

        return True

    # ========================================================================
    # Token Validation
    # ========================================================================

    async def validate_ticket(self) -> bool:
        """
        Validate the current ticket by making a lightweight API call.

        Returns:
            True if ticket is valid, False otherwise.
        """
        if not self.has_tokens():
            return False

        try:
            # Use PUT /v3/profiles/sessions as a lightweight validation
            headers = self._base_headers()
            headers["Authorization"] = f"Ubi_v1 t={self.tokens['ticket']}"
            session_id = self.tokens.get("sessionId", "")
            if session_id:
                headers["Ubi-SessionId"] = session_id

            session = await self._create_session()
            try:
                async with session.put(
                    AUTH_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        if "ticket" in body:
                            # Update tokens with refreshed data
                            tokens = self._extract_tokens(body)
                            tokens["userId"] = tokens.get("userId") or self.tokens.get("userId", "")
                            tokens["username"] = tokens.get("username") or self.tokens.get("username", "")
                            self._save_tokens(tokens)
                            return True
                    elif resp.status == 401:
                        logger.info("[Ubisoft API] Ticket invalid (401)")
                        return False
                    else:
                        logger.warning(f"[Ubisoft API] Ticket validation: HTTP {resp.status}")
                        return False
            finally:
                await session.close()

        except asyncio.TimeoutError:
            logger.warning("[Ubisoft API] Ticket validation timed out")
            # Assume valid if timeout (network issue, not auth issue)
            return True
        except Exception as e:
            logger.warning(f"[Ubisoft API] Ticket validation error: {e}")
            return True  # Assume valid on network errors

    # ========================================================================
    # GraphQL Library Query
    # ========================================================================

    async def get_owned_games(self) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch the user's owned games via GraphQL.

        Returns:
            List of game dicts with spaceId, name, coverUrl, etc.
            None on auth failure (caller should trigger re-auth).
            Empty list on API errors (graceful degradation).
        """
        if not await self.ensure_valid_token():
            logger.warning("[Ubisoft API] No valid token for library query")
            return None

        try:
            headers = self._base_headers()
            headers["Authorization"] = f"Ubi_v1 t={self.tokens['ticket']}"
            session_id = self.tokens.get("sessionId", "")
            if session_id:
                headers["Ubi-SessionId"] = session_id

            nodes = await self._fetch_owned_games_pages(headers)
            if nodes is None:
                logger.warning("[Ubisoft API] Library query: auth expired")
                if await self.refresh_token():
                    return await self._retry_library_query()
                return None
            return nodes

        except asyncio.TimeoutError:
            logger.error("[Ubisoft API] Library query timed out")
            return []
        except Exception as e:
            logger.exception(f"[Ubisoft API] Library query error: {e}")
            return []

    async def _fetch_owned_games_pages(
        self, headers: Dict[str, str]
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetch paginated library pages.

        Ubisoft currently rejects `viewer.games(limit)` values above 50, so
        fetch the library in 50-item pages until `totalCount` is satisfied.

        Returns:
            List of nodes on success.
            [] on non-auth API errors.
            None when the server returned 401/auth expired.
        """
        page_size = 50
        max_pages = 100
        nodes: List[Dict[str, Any]] = []
        offset = 0
        total: Optional[int] = None
        page_count = 0

        session = await self._create_session()
        try:
            while True:
                page_count += 1
                if page_count > max_pages:
                    logger.warning(
                        "[Ubisoft API] Library pagination aborted after "
                        f"{max_pages} pages without a terminating response"
                    )
                    break

                async with session.post(
                    GRAPHQL_URL,
                    headers=headers,
                    json={
                        "query": LIBRARY_QUERY,
                        "variables": {"limit": page_size, "offset": offset},
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 401:
                        return None

                    if resp.status != 200:
                        logger.error(
                            f"[Ubisoft API] Library query: HTTP {resp.status}"
                        )
                        return []

                    body = await resp.json()

                if body.get("errors"):
                    logger.error(
                        f"[Ubisoft API] Library query GraphQL error: "
                        f"{body['errors'][0].get('message', '')}"
                    )
                    return []

                games_data = (
                    body.get("data", {})
                    .get("viewer", {})
                    .get("games", {})
                )
                page_nodes = games_data.get("nodes", [])
                if total is None:
                    raw_total = games_data.get("totalCount")
                    total = raw_total if isinstance(raw_total, int) and raw_total > 0 else None

                nodes.extend(page_nodes)

                if (
                    not page_nodes
                    or len(page_nodes) < page_size
                    or (total is not None and len(nodes) >= total)
                ):
                    break

                offset += len(page_nodes)

            logger.info(
                f"[Ubisoft API] Library: {len(nodes)} games "
                f"(totalCount={total or len(nodes)})"
            )
            return nodes
        finally:
            await session.close()

    async def _retry_library_query(self) -> Optional[List[Dict[str, Any]]]:
        """Retry library query after token refresh."""
        if not self.tokens or "ticket" not in self.tokens:
            return None

        try:
            headers = self._base_headers()
            headers["Authorization"] = f"Ubi_v1 t={self.tokens['ticket']}"
            session_id = self.tokens.get("sessionId", "")
            if session_id:
                headers["Ubi-SessionId"] = session_id

            return await self._fetch_owned_games_pages(headers)
        except Exception as e:
            logger.error(f"[Ubisoft API] Retry library query failed: {e}")
            return None

    # ========================================================================
    # Logout
    # ========================================================================

    def logout(self) -> Dict[str, Any]:
        """
        Clear all auth state (tokens from memory and disk).

        Returns:
            {"success": True}
        """
        self._clear_tokens()
        logger.info("[Ubisoft API] Logged out")
        return {"success": True}

    # ========================================================================
    # Helpers
    # ========================================================================

    def _extract_tokens(self, response: Dict[str, Any], email: str = "") -> Dict[str, Any]:
        """Extract and structure token data from an auth response."""
        # Calculate refresh time from expiration
        expiration_str = response.get("expiration", "")
        server_time_str = response.get("serverTime", "")
        refresh_time = time.time() + 3600  # Default: refresh in 1 hour

        if expiration_str and server_time_str:
            try:
                # Parse ISO timestamps
                exp_time = datetime.fromisoformat(expiration_str.replace("Z", "+00:00")).timestamp()
                srv_time = datetime.fromisoformat(server_time_str.replace("Z", "+00:00")).timestamp()
                remaining = exp_time - srv_time
                # Refresh at 80% of remaining time
                refresh_time = time.time() + (remaining * 0.8)
            except (ValueError, TypeError) as e:
                logger.warning(f"[Ubisoft API] Could not parse token expiry: {e}")

        # Extract username from profiles if available
        username = email
        profiles = response.get("profiles", [])
        if profiles and isinstance(profiles, list):
            username = profiles[0].get("nameOnPlatform", email)

        return {
            "ticket": response.get("ticket", ""),
            "sessionId": response.get("sessionId", ""),
            "rememberMeTicket": response.get("rememberMeTicket", ""),
            "userId": response.get("userId", ""),
            "username": username,
            "refreshTime": refresh_time,
        }

    async def get_all_games(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch ALL games the user has any entitlement to.

        Since the Ubisoft GraphQL schema was simplified, this now returns
        the same data as ``get_owned_games()`` (``viewer { games }`` has
        no ``isOwned`` filter anymore).  Kept as an alias for callers
        that still reference it.
        """
        return await self.get_owned_games()

    async def get_subscription_games(self) -> List[Dict[str, Any]]:
        """Fetch Ubisoft+ subscription games (if user has active subscription).

        The vault API (api-uplayplusvault.ubi.com) is currently unreachable.
        This stub returns an empty list gracefully until the endpoint is
        discoverable or Ubisoft exposes subscription data through GraphQL.
        """
        VAULT_URL = "https://api-uplayplusvault.ubi.com/v1/games"

        if not await self.ensure_valid_token():
            return []

        try:
            headers = self._base_headers()
            headers["Authorization"] = f"Ubi_v1 t={self.tokens['ticket']}"

            session = await self._create_session()
            try:
                async with session.get(
                    VAULT_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        games = body if isinstance(body, list) else body.get("games", [])
                        logger.info(
                            f"[Ubisoft API] Subscription: {len(games)} games"
                        )
                        return games
                    logger.debug(
                        f"[Ubisoft API] Subscription endpoint: HTTP {resp.status}"
                    )
            finally:
                await session.close()
        except Exception as e:
            logger.debug(f"[Ubisoft API] Subscription endpoint unreachable: {e}")

        return []

    def get_user_id(self) -> Optional[str]:
        """Get the currently authenticated user's ID."""
        if self.tokens:
            return self.tokens.get("userId")
        return None

    def get_ticket(self) -> Optional[str]:
        """Get the current auth ticket."""
        if self.tokens:
            return self.tokens.get("ticket")
        return None

    def get_session_id(self) -> Optional[str]:
        """Get the current session ID."""
        if self.tokens:
            return self.tokens.get("sessionId")
        return None
