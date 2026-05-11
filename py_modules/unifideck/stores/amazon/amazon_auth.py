"""amazon_auth.py — Amazon Games OAuth via nile.

# OP-49b | py_modules/unifideck/stores/amazon/amazon_auth.py | Depends: OP-47b

Nile drives the OAuth dance itself in ``--non-interactive`` mode: we
run ``nile auth --login --non-interactive``, parse the login URL out
of its JSON stdout, hand the URL to :class:`AuthOrchestrator`, then
register the captured code via ``nile auth --register``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast

from ...auth.orchestrator import AuthOrchestrator
from ...core.types import AuthResult, Events, Result, StoreAuthError
from ...event_bus.event_bus import EventBus
from ...security import audit_auth_flow

logger = logging.getLogger(__name__)
_AMAZON_REDIRECT_URIS: list[str] = [
    'https://www.amazon.com/ap/maplanding',
    'https://amazon.com/ap/maplanding',
]


class AmazonAuthFlow:
    """Amazon auth flow."""

    def __init__(
        self,
        bus: EventBus,
        orchestrator: AuthOrchestrator,
        cli_path: str | None,
        success_markers: list[str],
        cli_timeout_seconds: int = 30,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._orchestrator = orchestrator
        self._cli_path = cli_path
        self._success_markers = success_markers
        self._cli_timeout_seconds = cli_timeout_seconds
        self._pending_login: dict[str, Any] | None = None

    @audit_auth_flow(store='amazon', method='oauth_cli')
    async def start_auth(self) -> AuthResult:
        """Start auth."""
        if not self._cli_path:
            return AuthResult(
                success=False, store='amazon', error='nile_not_found',
            )
        try:
            login_data = await self._fetch_login_url()
        except StoreAuthError as e:
            return AuthResult(
                success=False, store='amazon', error=str(e),
            )
        if not isinstance(login_data, dict):
            return AuthResult(
                success=False, store='amazon', error='nile_login_parse_failed',
            )
        url = str(login_data.get('url') or '')
        if not url:
            return AuthResult(
                success=False, store='amazon', error='nile_login_url_missing',
            )
        self._pending_login = login_data
        await self._orchestrator.start_browser_auth(
            url=url,
            allowed_redirect_uris=_AMAZON_REDIRECT_URIS,
            cookie_domain='amazon.com',
            on_code=self._register_code,
            store='amazon',
        )
        return AuthResult(success=True, store='amazon', redirect_url=url)

    async def logout(self) -> Result:
        """Logout."""
        if not self._cli_path:
            return Result(success=False, error='nile_not_found')
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, 'auth', '--logout',
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except OSError as e:
            return Result(success=False, error=f'spawn_failed:{e}')
        await self._bus.emit(Events.STORE_LOGOUT, store='amazon')
        return Result(success=True)

    async def _fetch_login_url(self) -> Any:
        """Fetch login URL."""
        login_data = await self._run_nile_login_probe()
        if login_data is None:
            raise StoreAuthError('nile_login_probe_failed', store='amazon')
        return login_data

    async def _run_nile_login_probe(self) -> dict[str, Any] | None:
        """Run NILE login probe."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, 'auth', '--login', '--non-interactive',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._cli_timeout_seconds,
            )
        except (TimeoutError, OSError) as e:
            logger.warning('[amazon_auth] login probe failed: %s', e)
            return None
        if proc.returncode != 0:
            logger.warning(
                '[amazon_auth] login probe rc=%s err=%s',
                proc.returncode,
                stderr.decode('utf-8', errors='replace')[:200],
            )
            return None
        try:
            data = json.loads(stdout.decode('utf-8', errors='replace'))
        except json.JSONDecodeError as e:
            logger.warning('[amazon_auth] login probe parse: %s', e)
            return None
        return cast(dict[str, Any], data) if isinstance(data, dict) else None

    async def _register_code(self, code: str) -> AuthResult:
        """Register code."""
        if not code:
            return AuthResult(
                success=False, store='amazon', error='no_auth_code',
            )
        if not self._cli_path:
            return AuthResult(
                success=False, store='amazon', error='nile_not_found',
            )
        if not self._pending_login:
            return AuthResult(
                success=False, store='amazon', error='no_pending_login',
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, 'auth', '--register', '--code', code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _out, err = await proc.communicate()
        except OSError as e:
            await self._bus.emit(
                Events.STORE_AUTH_FAILED, store='amazon', error=f'spawn:{e}',
            )
            return AuthResult(
                success=False, store='amazon', error=f'spawn:{e}',
            )
        self._pending_login = None
        if proc.returncode != 0:
            msg = err.decode('utf-8', errors='replace').strip() or 'auth_failed'
            await self._bus.emit(
                Events.STORE_AUTH_FAILED, store='amazon', error=msg,
            )
            return AuthResult(
                success=False, store='amazon', error=msg,
            )
        await self._bus.emit(Events.STORE_AUTH_COMPLETE, store='amazon')
        return AuthResult(success=True, store='amazon')
