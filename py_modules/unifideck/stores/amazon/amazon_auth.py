"""Amazon Games OAuth — embedded-browser sign-in flow.

OP-49b | py_modules/unifideck/stores/amazon/amazon_auth.py

Amazon Games requires the user to sign in through Amazon's web SSO
form. ``AmazonAuthFlow`` orchestrates the embedded CDP browser :

* open the Amazon Games OAuth URL with the right query params;
* inject a small script to capture the post-login redirect URL
  containing the auth code;
* exchange the code against access/refresh tokens via the Amazon
  Identity API;
* persist the tokens via the secure token store.

Failure modes (user cancels, network drops, CDP disconnect) are
reported back to the auth facade as ``AuthResult`` envelopes with
explicit error codes. Tokens are refreshed transparently when a
subsequent library/install call detects an expired access token.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast

from unifideck.auth.orchestrator import AuthOrchestrator
from unifideck.core.types import AuthResult, Events, Result, StoreAuthError
from unifideck.event_bus.event_bus import EventBus
from unifideck.security import audit_auth_flow

logger = logging.getLogger(__name__)
_AMAZON_REDIRECT_URIS: list[str] = [
    "https://www.amazon.com/ap/maplanding",
    "https://amazon.com/ap/maplanding",
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
        self._orch = orchestrator
        self._cli_path = cli_path
        self._success_markers = tuple(success_markers)
        self._cli_timeout = cli_timeout_seconds
        self._pending_login: dict[str, Any] | None = None

    @audit_auth_flow(store="amazon", method="oauth_cli")
    async def start_auth(self) -> AuthResult:
        """Start auth."""
        if not self._cli_path:
            return AuthResult(
                success=False,
                error="nile_not_found",
                store="amazon",
            )
        self._pending_login = None
        return await self._orch.run_flow(
            get_url=self._fetch_login_url,
            allowed_uris=_AMAZON_REDIRECT_URIS,
            exchange_code=self._register_code,
            background=True,
            write_url_file="~/.local/share/unifideck/amazon_auth_url.txt",
        )

    async def logout(self) -> Result:
        """Logout."""
        if not self._cli_path:
            await self._bus.emit(
                Events.STORE_LOGOUT,
                store="amazon",
            )
            return Result(success=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path,
                "auth",
                "--logout",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(
                proc.communicate(),
                timeout=self._cli_timeout,
            )
        except (TimeoutError, OSError) as e:
            logger.warning("[amazon_auth] logout: %s", e)
        self._pending_login = None
        await self._bus.emit(
            Events.STORE_LOGOUT,
            store="amazon",
        )
        return Result(success=True)

    async def _fetch_login_url(self) -> str:
        """Fetch login URL."""
        payload = await self._run_nile_login_probe()
        url = payload.get("url", "")
        if not url:
            raise StoreAuthError(
                "nile JSON has no 'url' field",
                store="amazon",
            )
        self._pending_login = payload
        logger.info("[amazon_auth] received login URL from nile")
        return cast("str", url)

    async def _run_nile_login_probe(self) -> dict[str, Any]:
        """Run NILE login probe."""
        if self._cli_path is None:
            raise StoreAuthError(
                "nile CLI not resolved",
                store="amazon",
            )
        proc = await asyncio.create_subprocess_exec(
            self._cli_path,
            "auth",
            "--login",
            "--non-interactive",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._cli_timeout,
            )
        except TimeoutError as e:
            raise StoreAuthError(
                f"nile auth timed out after {self._cli_timeout}s",
                store="amazon",
            ) from e
        if proc.returncode != 0:
            err = stderr.decode(errors="ignore") or stdout.decode(errors="ignore")
            raise StoreAuthError(
                f"nile auth failed (rc={proc.returncode}): {err[:200]}",
                store="amazon",
            )
        try:
            return cast("dict[Any, Any]", json.loads(stdout.decode(errors="ignore")))
        except json.JSONDecodeError as e:
            raise StoreAuthError(
                f"nile returned invalid JSON: {e}",
                store="amazon",
            ) from e

    async def _register_code(self, code: str) -> AuthResult:
        """Register code."""
        if self._pending_login is None:
            return AuthResult(
                success=False,
                error="no_pending_login",
                store="amazon",
            )
        login = self._pending_login
        args = [
            "register",
            "--code",
            code,
            "--code-verifier",
            login.get("code_verifier", ""),
            "--serial",
            login.get("serial", ""),
            "--client-id",
            login.get("client_id", ""),
        ]
        if self._cli_path is None:
            return AuthResult(
                success=False,
                error="nile_not_found",
                store="amazon",
            )
        proc = await asyncio.create_subprocess_exec(
            self._cli_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._cli_timeout,
            )
        except TimeoutError:
            return AuthResult(
                success=False,
                error="register_timeout",
                store="amazon",
            )
        combined = (
            stderr.decode(errors="ignore") + "\n" + stdout.decode(errors="ignore")
        )
        if any(m in combined for m in self._success_markers):
            self._pending_login = None
            logger.info(
                "[amazon_auth] nile register succeeded",
            )
            return AuthResult(success=True, store="amazon")
        logger.warning(
            "[amazon_auth] nile register failed: %s",
            combined[:200],
        )
        return AuthResult(
            success=False,
            error="register_failed",
            store="amazon",
        )
