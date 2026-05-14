"""Epic Games OAuth — embedded-browser sign-in flow.

OP-48b | py_modules/unifideck/stores/epic/auth.py

Epic Games requires the user to sign in through the Epic Games web
login. ``EpicAuthFlow`` orchestrates the embedded CDP browser :

* open the Epic OAuth URL with the right query params;
* inject a small script to capture the SID (session identifier)
  produced by Epic after a successful sign-in;
* exchange the SID against access/refresh tokens via Epic's account
  API;
* persist the tokens via the secure token store and forward them
  to ``legendary`` (which keeps its own credentials file separately).

Public methods cover the full lifecycle :

* ``start_auth(progress_cb)``  — open the browser and wait for SID;
* ``exchange_sid(sid)``        — convert SID → tokens;
* ``refresh_if_stale()``       — refresh expired access tokens;
* ``logout()``                 — wipe tokens locally and through
  legendary.

Failure modes (user cancels, network drops, CDP disconnect) are
reported back as ``AuthResult`` envelopes with explicit error codes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from unifideck.auth.orchestrator import AuthOrchestrator
from unifideck.core.types import AuthResult, Events, Result, StoreAuthError
from unifideck.event_bus.event_bus import EventBus
from unifideck.security import audit_auth_flow

logger = logging.getLogger(__name__)

_EPIC_REDIRECT_URIS: list[str] = [
    "https://legendary.epicgames.com/callback",
    "https://www.epicgames.com/id/api/redirect",
]

_AUTH_URL_MARKERS = ("epicgames.com",)


class EpicAuthFlow:
    """Epic auth flow."""

    def __init__(
        self,
        bus: EventBus,
        orchestrator: AuthOrchestrator,
        cli_path: str | None,
        cli_timeout_seconds: int = 30,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._orch = orchestrator
        self._cli_path = cli_path
        self._cli_timeout = cli_timeout_seconds

    @audit_auth_flow(store="epic", method="oauth_cli")
    async def start_auth(self) -> AuthResult:
        """Start auth."""
        if not self._cli_path:
            return AuthResult(
                success=False,
                error="legendary_not_found",
                store="epic",
            )
        return await self._orch.run_flow(
            get_url=self._fetch_login_url,
            allowed_uris=_EPIC_REDIRECT_URIS,
            exchange_code=self._register_code,
            background=True,
            write_url_file=("~/.local/share/unifideck/epic_auth_url.txt"),
        )

    async def logout(self) -> Result:
        """Logout."""
        if not self._cli_path:
            await self._bus.emit(
                Events.STORE_LOGOUT,
                store="epic",
            )
            return Result(success=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path,
                "auth",
                "--delete",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(
                proc.communicate(),
                timeout=self._cli_timeout,
            )
        except (TimeoutError, OSError) as e:
            logger.warning("[epic_auth] logout: %s", e)
        await self._bus.emit(
            Events.STORE_LOGOUT,
            store="epic",
        )
        return Result(success=True)

    async def _fetch_login_url(self) -> str:
        """Fetch login URL."""
        proc = await self._spawn_legendary_auth()
        try:
            url = await self._scrape_url_from_proc(proc)
        finally:
            await self._terminate_legendary(proc)
        if not url:
            raise StoreAuthError(
                "no OAuth URL found in legendary auth output",
                store="epic",
            )
        logger.info("[epic_auth] captured URL from legendary")
        return url

    async def _spawn_legendary_auth(self) -> Any:
        """Spawn LEGENDARY auth."""
        assert self._cli_path is not None, (
            "_spawn_legendary_auth called before CLI is resolved"
        )
        return await asyncio.create_subprocess_exec(
            self._cli_path,
            "auth",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    async def _scrape_url_from_proc(self, proc: Any) -> str | None:
        """Scrape URL from proc."""
        assert proc.stdout is not None
        deadline = asyncio.get_event_loop().time() + self._cli_timeout
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None
            try:
                line_bytes = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=remaining,
                )
            except TimeoutError:
                return None
            if not line_bytes:
                return None
            text = line_bytes.decode(errors="ignore").strip()
            url = self._extract_url(text)
            if url:
                return url
        return None

    @staticmethod
    async def _terminate_legendary(proc: Any) -> None:
        """Terminate LEGENDARY."""
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=2)
        except TimeoutError:
            proc.kill()
            await proc.wait()

    async def _register_code(self, code: str) -> AuthResult:
        """Register code."""
        assert self._cli_path is not None, (
            "_register_code called before CLI is resolved"
        )
        proc = await asyncio.create_subprocess_exec(
            self._cli_path,
            "auth",
            "--code",
            code,
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
                store="epic",
            )
        if proc.returncode == 0:
            logger.info("[epic_auth] legendary register succeeded")
            return AuthResult(success=True, store="epic")
        err = (stderr.decode(errors="ignore") or stdout.decode(errors="ignore"))[:200]
        logger.warning("[epic_auth] register failed: %s", err)
        return AuthResult(
            success=False,
            error="register_failed",
            store="epic",
        )

    @staticmethod
    def _extract_url(line: str) -> str | None:
        """Extract URL."""
        if "https://" not in line:
            return None
        for token in line.split():
            if not token.startswith("https://"):
                continue
            if any(m in token for m in _AUTH_URL_MARKERS):
                return token
        return None
