"""auth/orchestrator.py — Generic CDP OAuth flow orchestrator.
Four of Unifideck's five stores (Epic, GOG, Amazon, Microsoft)
share the same authentication pattern: a browser window opens
on a store-specific OAuth URL, the user signs in, the browser
redirects to a callback URL carrying an authorization code, the
code is exchanged for tokens via a store-specific mechanism, and
the tab is closed.
Rather than duplicate the wait/close/emit boilerplate in each
store, this module provides AuthOrchestrator — a stateless helper
that takes two callables (get_url, exchange_code) and performs
the common sequence. Each store keeps its specifics inside the
callables it passes in.
Ubisoft is the exception: its auth uses a Wine prefix + UPC
session file, not CDP OAuth. Ubisoft does not use this module.
Two modes of operation:
 Blocking (legacy): run_flow() awaits until the user has
 either finished signing in, hit a failure, or timed out.
 Background (for RunGame launches): run_flow() returns
 immediately with AuthResult(pending=True) once the URL has
 been obtained and (optionally) written to disk for the shell
 launcher to read. A background task then waits for the
 redirect, exchanges the code, and emits the terminal event.
The `pending` flag on AuthResult tells the frontend "auth
started, listen for AUTH_COMPLETE/AUTH_FAILED events to know
the outcome". All four OAuth stores now use this mode because
the frontend needs to call steamApps.RunGame() right after the
RPC returns, and can't afford to block the RPC on the user's
OAuth flow.
Reference: Technical Document v1.0 — Section 3.4.4.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from unifideck.core.types import AuthResult, Events

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus

    from .browser import OAuthBrowserMonitor
    # Type aliases for the store-specific callbacks. Keeping them
    # explicit makes the contract between the orchestrator and its
    # callers obvious at the type level.
    # get_url() → returns the OAuth URL (or raises on failure). The
    # store is free to do anything here: call a CLI, parse a config
    # file, hit an HTTP endpoint, return a hardcoded string.
    GetUrlCallback = Callable[[], Awaitable[str]]
    # exchange_code(code) → completes the token exchange using the
    # captured OAuth code. Returns an AuthResult reflecting the
    # outcome. Stores use this to call their CLI's `register`
    # command, run a token chain, or persist a session file.
    ExchangeCodeCallback = Callable[[str], Awaitable[AuthResult]]

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    """Tunable parameters for one auth flow execution."""

    # Total wallclock deadline for the full flow in seconds.
    timeout: float = 300.0
    # Grace period between URL handoff and monitor start. Gives
    # the shell launcher time to spawn Edge before we begin
    # polling for CDP targets. Does not affect correctness.
    browser_launch_grace: float = 1.5

class AuthOrchestrator:
    """Stateless orchestrator for CDP OAuth auth flows.
    One instance per store. The store-specific state lives in
    the store's own auth module and is captured by the
    get_url/exchange_code closures passed to run_flow().
    """

    def __init__(
        self,
        bus: EventBus,
        browser_monitor: OAuthBrowserMonitor,
        store_name: str,
        config: OrchestratorConfig | None = None,
    ) -> None:
        """Initialise with the shared services the flow needs.

        Args:
        bus: EventBus for emitting AUTH_STARTED / COMPLETE /
        FAILED. The orchestrator never subscribes.
        browser_monitor: Shared OAuthBrowserMonitor used to
        poll CDP targets and match redirect URLs.
        store_name: Store identifier for events and logs.
        config: Optional tuning. Defaults are safe.

        """
        self._bus = bus
        self._monitor = browser_monitor
        self._store = store_name
        self._cfg = config or OrchestratorConfig()
        # Background task handle — set when running in background
        # mode so logout/cancel can stop a stale flow cleanly.
        self._bg_task: asyncio.Task | None = None
        # ─── Public API ────────────────────────────────────────────

    async def run_flow(
        self,
        get_url: GetUrlCallback,
        allowed_uris: list[str],
        exchange_code: ExchangeCodeCallback,
        *,
        timeout: float | None = None,
        write_url_file: str | None = None,
        background: bool = False,
    ) -> AuthResult:
        """Execute the CDP OAuth flow (blocking or background).

        Args:
        get_url: Coroutine returning the OAuth URL.
        allowed_uris: Redirect URL prefixes signalling success.
        exchange_code: Coroutine that takes the captured code
        and returns the terminal AuthResult.
        timeout: Optional override for the flow deadline.
        write_url_file: Optional path where the URL should be
        written on disk (for the shell launcher to read).
        The write is atomic (.tmp then rename).
        background: If True, return early after URL acquisition
        and run the rest of the flow in a background task.
        The returned AuthResult has pending=True.

        Returns:
        Blocking mode: the terminal AuthResult.
        Background mode: AuthResult(success=True, pending=True)
        unless URL acquisition itself failed.

        """
        deadline = timeout if timeout is not None else self._cfg.timeout
        # Announce the attempt.
        await self._emit_started()
        # Step 1: get the URL (store-specific, always synchronous).

        try:
            url = await get_url()
        except Exception as e:
            logger.exception("[AuthOrchestrator/%s] get_url failed", self._store)
            return await self._emit_failed(
                "get_url_failed", str(e),
            )
        if not url:
            return await self._emit_failed(
                "no_url", "get_url returned empty string",
            )
        # Step 2: optionally persist the URL for the shell
        # launcher.
        if write_url_file:
            write_ok = await self._write_url_atomically(
                write_url_file, url,
            )
            if not write_ok:
                return await self._emit_failed(
                    "url_write_failed",
                    f"could not write URL to {write_url_file}",
                    url=url,
                )
        # Step 3: hand off to blocking or background mode.
        if background:
            return self._spawn_background_task(
                url=url,
                allowed_uris=allowed_uris,
                exchange_code=exchange_code,
                deadline=deadline,
            )
        return await self._await_redirect_and_exchange(
            url=url,
            allowed_uris=allowed_uris,
            exchange_code=exchange_code,
            deadline=deadline,
        )

    def cancel_background(self) -> bool:
        """Cancel the in-flight background auth task if any.
        Used by logout() to stop a stale monitor when the user
        explicitly clears their credentials during an auth flow.

        Returns:
        True if a task was cancelled, False otherwise.

        """
        task = self._bg_task
        if task is None or task.done():
            return False
        task.cancel()
        self._bg_task = None
        return True
            # ─── Core flow ────────────────────────────────────────────

    async def _await_redirect_and_exchange(
        self,
        url: str,
        allowed_uris: list[str],
        exchange_code: ExchangeCodeCallback,
        deadline: float,
    ) -> AuthResult:
        """Wait for the CDP redirect and exchange the code.

        Shared between blocking and background modes. Never
        raises under normal operation — all errors become
        AuthResult failures with the appropriate event emission.
        CancelledError is re-raised so background mode can stop
        cleanly on logout.
        """
        logger.info(
            "[AuthOrchestrator/%s] waiting for redirect to %s "
            "(timeout=%.0fs)",
            self._store, allowed_uris, deadline,
        )
        # Grace period so the browser has time to spawn before
        # we start polling CDP targets.
        await asyncio.sleep(self._cfg.browser_launch_grace)

        try:
            capture = await self._monitor.wait_for_redirect(
                allowed_uris=allowed_uris,
                timeout=deadline,
            )
        except asyncio.CancelledError:
            logger.info(
                "[AuthOrchestrator/%s] flow cancelled", self._store,
            )
            raise
        except Exception as e:
            logger.exception("[AuthOrchestrator/%s] monitor crashed", self._store)
            return await self._emit_failed("monitor_crashed", str(e))

        if not capture.success:
            return await self._emit_failed(
                capture.error or "capture_failed",
                f"browser capture failed after "
                f"{capture.elapsed_seconds:.1f}s",
                url=url,
            )
        code = capture.code
        if not code:
            return await self._emit_failed(
                "no_code",
                f"redirect to {capture.redirect_url} carried no "
                f"`code` parameter",
                url=url,
            )
        # Close the OAuth tab before the exchange so the user
        # sees the browser disappear quickly on success.
        await self._close_tab_safely(capture.redirect_url)
        return await self._finalize_auth_exchange(
            code, url, exchange_code,
        )

    async def _finalize_auth_exchange(
        self,
        code: str,
        url: str,
        exchange_code: ExchangeCodeCallback,
    ) -> AuthResult:
        """Run the store-specific code exchange and emit the terminal event.

        Wraps the exchange in defensive error handling so the
        orchestrator never leaks an exception to its callers,
        and fires either ``STORE_AUTH_COMPLETE`` or
        ``STORE_AUTH_FAILED`` based on the outcome.
        """
        try:
            result = await exchange_code(code)
        except Exception as e:
            logger.exception("[AuthOrchestrator/%s] exchange_code failed", self._store)
            return await self._emit_failed(
                "exchange_failed", str(e), url=url,
            )
        result.store = self._store
        if result.success:
            await self._bus.emit(
                Events.STORE_AUTH_COMPLETE, store=self._store,
            )
            logger.info(
                "[AuthOrchestrator/%s] auth complete", self._store,
            )
        else:
            await self._bus.emit(
                Events.STORE_AUTH_FAILED,
                store=self._store,
                error=result.error or "exchange_returned_failure",
            )
            logger.warning(
                "[AuthOrchestrator/%s] exchange failed: %s",
                self._store, result.error,
            )
        return result


    def _spawn_background_task(
        self,
        url: str,
        allowed_uris: list[str],
        exchange_code: ExchangeCodeCallback,
        deadline: float,
    ) -> AuthResult:
        """Create the asyncio task for background mode and return.

        Captures the task handle on self._bg_task so logout can
        cancel it later. Returns AuthResult(pending=True) at
        once so the frontend can proceed to RunGame().
        """
        # If a previous background flow is still in flight,
        # cancel it before starting a new one. This handles the
        # case where the user clicks "Sign in" twice quickly.
        self.cancel_background()

        async def _background_runner() -> None:
            try:
                await self._await_redirect_and_exchange(
                    url=url,
                    allowed_uris=allowed_uris,
                    exchange_code=exchange_code,
                    deadline=deadline,
                )
            except asyncio.CancelledError:
                # task cancelled mid-flight; swallow to let shutdown proceed
                pass  # expected on explicit logout / re-auth
            finally:
                if self._bg_task is not None and self._bg_task.done():
                    self._bg_task = None

        self._bg_task = asyncio.create_task(
            _background_runner(),
            name=f"auth_flow_{self._store}",
        )
        logger.info(
            "[AuthOrchestrator/%s] background flow started",
            self._store,
        )
        return AuthResult(
            success=True,
            store=self._store,
            url=url,
            metadata={"pending": True},
        )

    # ─── Event helpers ────────────────────────────────────────

    async def _emit_started(self) -> None:
        """Announce the start of the flow on the EventBus."""
        await self._bus.emit(Events.STORE_AUTH_STARTED, store=self._store)

    async def _emit_failed(
        self,
        error_code: str,
        detail: str,
        url: str | None = None,
    ) -> AuthResult:
        """Emit AUTH_FAILED and build a matching AuthResult."""
        logger.warning(
            "[AuthOrchestrator/%s] %s: %s",
            self._store, error_code, detail,
        )
        await self._bus.emit(
            Events.STORE_AUTH_FAILED,
            store=self._store,
            error=error_code,
        )
        return AuthResult(
            success=False,
            error=error_code,
            store=self._store,
            url=url,
        )

    async def _close_tab_safely(self, url_substring: str | None) -> None:
        """Attempt to close the OAuth tab; never raise on failure.

        Tab closure is best-effort cleanup. If it fails (browser
        dead, CDP port closed, tab already navigated away) the
        flow is still successful — we log and move on.
        """
        try:
            if url_substring is None:
                return
            domain = url_substring
            if "://" in domain:
                domain = domain.split("://", 1)[1]
            if "/" in domain:
                domain = domain.split("/", 1)[0]
            await self._monitor.close_oauth_tab(domain)
        except Exception as e:  # noqa: BLE001 — project pattern: catch-log-continue for runtime resilience
            logger.debug(
                "[AuthOrchestrator/%s] close_oauth_tab failed "
                "(ignored): %s",
                self._store, e,
            )

    # ─── I/O helpers ──────────────────────────────────────────

    @staticmethod
    async def _write_url_atomically(path: str, url: str) -> bool:
        """Write the OAuth URL to disk atomically.

        Creates the parent directory if needed, writes to a
        `.tmp` sibling first, then renames into place. This
        guarantees the shell launcher never reads a half-written
        URL file.
        """
        def _write_sync() -> str:
            expanded = Path(path).expanduser()
            parent = expanded.parent
            parent.mkdir(parents=True, exist_ok=True)
            tmp = expanded.with_name(expanded.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                f.write(url)
            tmp.replace(expanded)
            return str(expanded)

        try:
            expanded = await asyncio.to_thread(_write_sync)
            logger.debug(
                "[AuthOrchestrator] wrote auth URL to %s", expanded,
            )
            return True
        except OSError:
            logger.exception("[AuthOrchestrator] failed to write %s", expanded)
            return False
