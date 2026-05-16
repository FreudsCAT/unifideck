from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from unifideck.auth.orchestrator import AuthOrchestrator
from unifideck.core.types import AuthResult, Events, Result
from unifideck.event_bus.event_bus import EventBus
from unifideck.security import audit_auth_flow
from unifideck.utils.locale import get_unifideck_locale

from .microsoft_config import MicrosoftConfig
from .tokens import MicrosoftTokenManager

logger = logging.getLogger(__name__)
_MS_AUTH_URL_FILE = "~/.local/share/unifideck/ms_auth_url.txt"
class MicrosoftBrowserAuth:
    """Microsoft browser auth."""
    def __init__(
    self,
    bus: EventBus,
    orchestrator: AuthOrchestrator,
    tokens: MicrosoftTokenManager,
    config: MicrosoftConfig,
    config_manager: Any,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._orch = orchestrator
        self._tokens = tokens
        self._config = config
        self._config_manager = config_manager
    @audit_auth_flow(store="microsoft", method="oauth_browser")
    async def start_auth(self) -> AuthResult:
        """Start auth."""
        if not self._config.is_valid():
            return AuthResult(
                success=False,
                error="config_invalid",
                store="microsoft",
            )
        return await self._orch.run_flow(
            get_url=self._build_auth_url,
            allowed_uris=list(
                self._config.allowed_redirect_uris,
            ),
            exchange_code=self._exchange_code,
            background=True,
            write_url_file=_MS_AUTH_URL_FILE,
        )
    async def _build_auth_url(self) -> str:
        """Build auth URL."""
        locale = get_unifideck_locale(self._config_manager)
        params = {
        "client_id": self._config.client_id,
        "redirect_uri": self._config.redirect_uri,
        "response_type": "code",
        "scope": self._config.scope,
        "ui_locales": locale,
        }
        query = urllib.parse.urlencode(params, safe="/: ")
        url = f"{self._config.auth_url}?{query}"
        logger.info(
            "[MicrosoftBrowserAuth] built OAuth URL: %s"
            "?client_id=REDACTED&redirect_uri=%s&scope=%s"
            " (locale=%s)",
            self._config.auth_url,
            self._config.redirect_uri,
            self._config.scope,
            locale,
        )
        return url

    async def _exchange_code(self, code: str) -> AuthResult:

        """Exchange code."""
        ok = await self._tokens.exchange_code(code)
        if ok:
            logger.info(
                "[MicrosoftBrowserAuth] token exchange successful",
            )
            return AuthResult(success=True, store="microsoft")
        return AuthResult(
            success=False,
            error="token_exchange_failed",
            store="microsoft",
        )
    async def logout(self) -> Result:
        """Logout."""
        self._orch.cancel_background()
        await self._tokens.clear()
        await self._bus.emit(
        Events.STORE_LOGOUT, store="microsoft",
        )
        return Result(success=True)
