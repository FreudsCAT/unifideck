"""auth.py — Browser-mediated OAuth for GOG Galaxy.

# OP-50h | py_modules/unifideck/stores/gog/auth.py | Depends: OP-47b

GOG uses the Galaxy desktop-client OAuth flow: we open the user's
browser to the auth URL with a redirect to ``embed.gog.com`` which the
:class:`AuthOrchestrator` watches via CDP. On redirect we extract the
``code`` query-param, hand it to :class:`GOGTokenManager.exchange_code`
which mints access + refresh tokens.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from ...auth.orchestrator import AuthOrchestrator
from ...core.types import AuthResult, Events, Result
from ...event_bus.event_bus import EventBus
from ...security import audit_auth_flow
from .config import GOG_AUTH_URL_FILE, GOGConfig
from .tokens import GOGTokenManager

logger = logging.getLogger(__name__)
_GOG_COOKIE_DOMAIN = 'gog.com'


class GOGBrowserAuth:
    """GOG browser auth."""

    def __init__(
        self,
        bus: EventBus,
        orchestrator: AuthOrchestrator,
        tokens: GOGTokenManager,
        config: GOGConfig,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._orchestrator = orchestrator
        self._tokens = tokens
        self._config = config

    @audit_auth_flow(store='gog', method='oauth_browser')
    async def start_auth(self) -> AuthResult:
        """Start auth."""
        url = await self._build_auth_url()
        if not url:
            return AuthResult(
                success=False, store='gog', error='auth_config_invalid',
            )
        try:
            self._write_url_hint(url)
        except OSError as e:
            logger.debug('[GOGAuth] url hint write: %s', e)
        await self._orchestrator.start_browser_auth(
            url=url,
            allowed_redirect_uris=self._config.allowed_redirect_uris,
            cookie_domain=_GOG_COOKIE_DOMAIN,
            on_code=self._exchange_code,
            store='gog',
        )
        return AuthResult(
            success=True, store='gog', redirect_url=url,
        )

    async def _build_auth_url(self) -> str:
        """Build auth URL."""
        if not self._config.auth_url or not self._config.client_id:
            return ''
        params = {
            'client_id': self._config.client_id,
            'redirect_uri': self._config.redirect_uri,
            'response_type': 'code',
            'layout': 'galaxy',
        }
        return f'{self._config.auth_url}?{urllib.parse.urlencode(params)}'

    @staticmethod
    def _write_url_hint(url: str) -> None:
        """Write URL hint."""
        import os
        path = os.path.expanduser(GOG_AUTH_URL_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(url + '\n')

    async def _exchange_code(self, code: str) -> AuthResult:
        """Exchange code."""
        if not code:
            return AuthResult(
                success=False, store='gog', error='no_auth_code',
            )
        ok = await self._tokens.exchange_code(code)
        if not ok:
            await self._bus.emit(
                Events.STORE_AUTH_FAILED, store='gog', error='exchange_failed',
            )
            return AuthResult(
                success=False, store='gog', error='exchange_failed',
            )
        await self._bus.emit(Events.STORE_AUTH_COMPLETE, store='gog')
        return AuthResult(success=True, store='gog')

    async def logout(self, browser_monitor: Any | None = None) -> Result:
        """Logout."""
        await self._tokens.clear()
        if browser_monitor is not None:
            try:
                await browser_monitor.clear_cookies(_GOG_COOKIE_DOMAIN)
            except Exception as e:
                logger.debug('[GOGAuth] cookie clear: %s', e)
        await self._bus.emit(Events.STORE_LOGOUT, store='gog')
        return Result(success=True)
