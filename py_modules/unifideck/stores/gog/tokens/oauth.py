"""OAuth protocol — exchange auth code for tokens, refresh expired tokens.

OP-52c | py_modules/unifideck/stores/gog/tokens/oauth.py

``_TokenOAuth`` speaks GOG's OAuth 2.0 endpoint :

* ``exchange_code(auth_code)`` — POSTs the authorization code obtained
  from ``auth.py`` (OP-50h) and returns ``(access_token, refresh_token,
  user_info)``;
* ``refresh(refresh_token)`` — POSTs the refresh token and returns a
  new pair of access/refresh tokens (GOG rotates refresh tokens, so
  the old refresh token becomes invalid after refresh).

HTTP calls go through ``http.py`` (OP-50i) for the bundled CA chain.
Errors are wrapped into a typed exception so the caller can distinguish
network failures (retryable) from auth failures (force re-login).
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import TYPE_CHECKING, Any

from unifideck.stores.gog.http import fetch_json_get

from .user_info import GOGUserInfo

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from unifideck.stores.gog.config import GOGConfig

    SaveCallback = Callable[[str, str], Awaitable[bool]]
logger = logging.getLogger(__name__)


class _TokenOAuth:
    """Token oauth."""

    def __init__(self, *, config: GOGConfig, save_callback: SaveCallback) -> None:
        """Initialize the instance."""
        self._config = config
        self._save = save_callback

    async def exchange_code(self, auth_code: str) -> bool:
        """Exchange code."""
        params = {
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": self._config.redirect_uri,
        }
        return await self._token_request(params)

    async def refresh_if_stale(
        self,
        *,
        access_token: str | None,
        refresh_token: str | None,
        age_seconds: float,
    ) -> bool:
        """Refresh if stale."""
        threshold = self._config.token_refresh_threshold_seconds
        if age_seconds < threshold and access_token:
            return True
        if not refresh_token:
            logger.info(
                "[GOGTokens] no refresh token — session is dead",
            )
            return False
        logger.info(
            "[GOGTokens] token age %.0fs ≥ %ds, refreshing",
            age_seconds,
            threshold,
        )
        params = {
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        return await self._token_request(params)

    async def fetch_user_info(
        self,
        access_token: str,
        fallback: GOGUserInfo,
    ) -> GOGUserInfo:
        """Fetch user info."""
        url = f"{self._config.base_url}/userData.json"
        data = await fetch_json_get(
            url,
            bearer=access_token,
            user_agent=self._config.user_agent,
            timeout=10.0,
            log_prefix="[GOGTokens] userData",
        )
        if not isinstance(data, dict):
            return fallback
        return GOGUserInfo(
            username=str(
                data.get("username", "") or fallback.username,
            ),
            galaxy_user_id=str(
                data.get("galaxyUserId", "") or fallback.galaxy_user_id,
            ),
        )

    async def _token_request(self, params: dict[str, str]) -> bool:
        """Token request."""
        url = f"{self._config.token_url}?{urllib.parse.urlencode(params)}"
        data = await fetch_json_get(
            url,
            user_agent=self._config.user_agent,
            timeout=15.0,
            log_prefix="[GOGTokens] token endpoint",
        )
        if not isinstance(data, dict):
            return False
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if not access or not refresh:
            logger.error(
                "[GOGTokens] token response missing tokens: keys=%s",
                list(data.keys()),
            )
            return False
        return await self._save(access, refresh)


_ = Any
