from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from unifideck.stores.microsoft.microsoft_auth import http_post

if TYPE_CHECKING:
    from unifideck.stores.microsoft.microsoft_config import MicrosoftConfig
logger = logging.getLogger(__name__)
class OAuthMixin:
    """Oauth mixin."""
    _ms_access_token: str | None
    _ms_refresh_token: str | None
    _token_saved_at: float
    _config: MicrosoftConfig
    async def exchange_code(self, auth_code: str) -> bool:
        """Exchange code."""
        return await self._token_request({
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "code": auth_code,
            "grant_type": "authorization_code",
            "scope": self._config.scope,
        })
    async def refresh_if_stale(self) -> bool:
        """Refresh if stale."""
        age = time.time() - self._token_saved_at
        threshold = self._config.token_refresh_threshold_seconds
        if age < threshold and self._ms_access_token:
            return True
        if not self._ms_refresh_token:
            logger.error(
                "[MicrosoftTokens] refresh needed but no "
                "refresh token available — session dead",
            )
            return False
        logger.info(
            "[MicrosoftTokens] refreshing access token "
            "(age=%.0fs)",
            age,
        )
        return await self._token_request({
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "refresh_token": self._ms_refresh_token,
            "grant_type": "refresh_token",
            "scope": self._config.scope,
        })

    async def _token_request(
        self, params: dict[str, str],
    ) -> bool:

        """Token request."""
        headers = {
            "Content-Type":
                "application/x-www-form-urlencoded",
        }
        try:
            token_data = await (
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: http_post(
                        self._config.token_url,
                        params, headers,
                    ),
                )
            )
        except Exception:
            logger.exception("[MicrosoftTokens] token HTTP failed")
            return False
        if (
            not isinstance(token_data, dict)
            or "access_token" not in token_data
        ):
            # `(token_data or {}).get(...)`
            # raised an uncaught AttributeError when http_post
            # returned a truthy non-dict (e.g. a non-empty
            # string error body): `"str" or {}` evaluates to
            # the string, which has no `.get`. A falsy non-dict
            # ("" / None) was handled correctly; only the
            # truthy non-dict case was the latent bug. Guard on
            # isinstance so any non-dict degrades to a logged
            # rejection returning False.
            error = (
                token_data.get("error", "unknown")
                if isinstance(token_data, dict)
                else "unknown"
            )
            logger.error(
                "[MicrosoftTokens] token endpoint rejected "
                "request: %s", error,
            )
            return False
        self._ms_access_token = token_data["access_token"]
        new_refresh = token_data.get("refresh_token")
        if new_refresh:
            self._ms_refresh_token = new_refresh
        self._token_saved_at = time.time()
        await self.save()  # type: ignore[attr-defined]  # self.save provided by sibling mixin _PersistenceMixin
        return True
