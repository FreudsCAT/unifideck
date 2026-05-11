"""oauth.py — GOG OAuth code/refresh exchanger.

# OP-52c | py_modules/unifideck/stores/gog/tokens/oauth.py | Depends: (none)

Handles the three OAuth verbs GOG cares about — auth_code exchange,
silent refresh, and user_info fetch — using :mod:`..http` underneath.
The ``save_callback`` is invoked whenever new tokens are minted so
the :class:`_TokenStorage` can persist them.
"""
from __future__ import annotations

import logging
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

from ..http import fetch_json_get
from .user_info import GOGUserInfo

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..config import GOGConfig
    SaveCallback = Callable[[str, str], Awaitable[bool]]

logger = logging.getLogger(__name__)


class _TokenOAuth:
    """Token oauth."""

    def __init__(
        self, *, config: GOGConfig, save_callback: SaveCallback,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._save_callback = save_callback
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_minted_at: float = 0.0

    async def exchange_code(self, auth_code: str) -> bool:
        """Exchange code."""
        if not auth_code or not self._config.token_url:
            return False
        params = {
            'client_id': self._config.client_id,
            'client_secret': self._config.client_secret,
            'grant_type': 'authorization_code',
            'code': auth_code,
            'redirect_uri': self._config.redirect_uri,
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
        self._access_token = access_token
        self._refresh_token = refresh_token
        if not refresh_token:
            return False
        if age_seconds < self._config.token_refresh_threshold_seconds:
            return True
        params = {
            'client_id': self._config.client_id,
            'client_secret': self._config.client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }
        return await self._token_request(params)

    async def fetch_user_info(
        self, access_token: str, fallback: GOGUserInfo,
    ) -> GOGUserInfo:
        """Fetch user info."""
        if not access_token or not self._config.api_gog_url:
            return fallback
        payload = await fetch_json_get(
            f'{self._config.api_gog_url}/user/data/account',
            bearer=access_token,
            user_agent=self._config.user_agent,
            log_prefix='[GOGOAuth]',
        )
        if not isinstance(payload, dict):
            return fallback
        username = payload.get('username') or payload.get('email') or ''
        galaxy_user_id = (
            str(payload.get('galaxyUserId') or payload.get('userId') or '')
        )
        return GOGUserInfo(
            username=str(username),
            galaxy_user_id=galaxy_user_id,
        )

    async def _token_request(self, params: dict[str, str]) -> bool:
        """Token request."""
        url = (
            f'{self._config.token_url}?{urllib.parse.urlencode(params)}'
        )
        payload = await fetch_json_get(
            url,
            user_agent=self._config.user_agent,
            log_prefix='[GOGOAuth]',
        )
        if not isinstance(payload, dict):
            return False
        access = payload.get('access_token')
        refresh = payload.get('refresh_token')
        if not isinstance(access, str) or not isinstance(refresh, str):
            return False
        self._access_token = access
        self._refresh_token = refresh
        self._token_minted_at = time.time()
        return await self._save_callback(access, refresh)


_: Any = None
