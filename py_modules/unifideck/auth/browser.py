"""auth/browser.py — OAuth code capture via CDP page monitoring.

While the user logs in to a store via the in-Steam embedded
browser, this module watches the browser's tab list (via CDP)
and captures the authorization code from the redirect URL the
moment it appears.

Lot 13a (file-cap split): the implementation is spread across
four files to keep each under the 550-line volumetry gate:

* ``browser_types`` — ``AuthCaptureResult`` dataclass.
* ``browser_url_parsing`` — URL helpers, redirect match,
  code-from-URL extraction, capture-result builders.
* ``browser_content`` — page-body extraction via CDP websocket
  (Epic JSON blob, generic caller-supplied patterns).
* ``browser`` (this file) — ``OAuthBrowserMonitor`` class
  orchestrating the polling loop and CDP endpoints.

This module re-exports ``AuthCaptureResult``, ``extract_oauth_params``
and ``match_redirect`` for backward compatibility — every previous
``from unifideck.auth.browser import …`` still works.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from unifideck.utils.config_helpers import get_cfg

# Re-exports for backward compatibility with prior import paths.
from .browser_content import (
    try_content_fallback,
    try_epic_content_capture,
)
from .browser_types import AuthCaptureResult
from .browser_url_parsing import (
    build_redirect_capture,
    build_url_code_capture,
    extract_code_from_url,
    extract_oauth_params,
    is_oauth_relevant_url,
    match_redirect,
)

if TYPE_CHECKING:
    from unifideck.cdp.cdp_client import CDPClient
    from unifideck.config import ConfigManager


logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 0.5  # seconds between tab list checks
DEFAULT_OAUTH_TIMEOUT = 300  # seconds


# Public re-exports — keep the module's import surface stable
# even though the implementation has moved into siblings.
__all__ = [
    "AuthCaptureResult",
    "CDPOAuthMonitor",
    "DEFAULT_OAUTH_TIMEOUT",
    "DEFAULT_POLL_INTERVAL",
    "OAuthBrowserMonitor",
    "extract_oauth_params",
    "match_redirect",
]


class OAuthBrowserMonitor:
    """Watches Steam's embedded browser and Edge for an OAuth redirect.

    Polls both the Steam CEF CDP endpoint (port 8080) and the
    Edge CDP endpoint (port 9222) every ``poll_interval`` seconds
    until a tab lands on a URL matching the allowed redirect
    list. Bounded by a timeout so the user can abandon the auth
    flow without leaving the plugin hanging.

    The dual-endpoint design exists because the OAuth browser
    is a standalone Edge instance (launched by the shortcut
    helper), NOT a Steam CEF tab — but the CEF port was the
    only port monitored pre-0.7, so redirects were never
    detected and auth timed out after 300 s.
    """

    def __init__(
        self,
        cdp_client: CDPClient,
        config: ConfigManager | None = None,
        edge_cdp_port: int = 9222,
    ) -> None:
        self._cdp = cdp_client
        self._config = config
        self._edge_cdp_port = edge_cdp_port
        self._poll_interval = float(get_cfg(
            config, "auth.browser_poll_interval_seconds",
            DEFAULT_POLL_INTERVAL,
        ))
        self._default_timeout = float(get_cfg(
            config, "auth.browser_oauth_timeout_seconds",
            DEFAULT_OAUTH_TIMEOUT,
        ))

    # ── Public API ─────────────────────────────────────────────

    async def wait_for_redirect(
        self,
        allowed_uris: list[str],
        timeout: float | None = None,  # noqa: ASYNC109 — internal polling deadline
        *,
        content_trigger_url: str | None = None,
        content_regex: str | None = None,
    ) -> AuthCaptureResult:
        """Block until a browser tab navigates to an allowed URI.

        Returns as soon as any tab in Steam's CEF process **or**
        the Edge auth browser matches one of the prefixes in
        ``allowed_uris``. If the timeout elapses first, returns
        a failure result.

        When ``content_trigger_url`` and ``content_regex`` are
        both set, *also* scans page content for a matching
        group: if any target's URL contains
        ``content_trigger_url``, the method connects to that
        target via CDP, evaluates ``document.body.innerText``,
        and applies the regex. This is used as a fallback for
        stores (e.g. Epic) that embed the authorization code in
        a JSON blob inside the page body rather than a query
        parameter.

        Refactor history (lot 13a):

        1. Cognitive-complexity refactor: originally a single
           method at CCR=63, decomposed into private helpers
           (``_poll_both_endpoints``, ``_log_heartbeat_if_due``,
           ``_try_capture_target``).
        2. File-cap split: content extraction and URL helpers
           moved to sibling modules. This orchestrator stays
           focused on the polling loop and CDP endpoints.
        """
        deadline = time.monotonic() + (
            timeout if timeout is not None
            else self._default_timeout
        )
        start = time.monotonic()
        # Mutable polling state passed by reference to helpers.
        # ``monitored_urls`` dedups URL inspection across loop
        # iterations; ``logged_urls`` dedups the "OAuth page
        # detected" info log; ``content_extract_first_attempt``
        # tracks which URLs we've already content-extracted from
        # at least once (to escalate failure logging from DEBUG
        # to INFO only on the first attempt per URL).
        state: dict[str, Any] = {
            "monitored_urls": set(),
            "logged_urls": set(),
            "content_extract_first_attempt": set(),
            "last_heartbeat": 0.0,
        }
        while time.monotonic() < deadline:
            cef_targets, edge_targets = await self._poll_both_endpoints()
            all_targets = cef_targets + edge_targets
            self._log_heartbeat_if_due(
                state, cef_targets, edge_targets, all_targets,
            )

            for target in all_targets:
                result = await self._try_capture_target(
                    target, allowed_uris, state, start,
                )
                if result is not None:
                    return result

            fallback = await try_content_fallback(
                all_targets, content_trigger_url, content_regex, start,
            )
            if fallback is not None:
                return fallback

            await asyncio.sleep(self._poll_interval)

        monitored = len(state["monitored_urls"])
        logger.warning(
            "[auth/browser] timeout after %.1fs — "
            "no code found in %d unique URLs",
            time.monotonic() - start, monitored,
        )
        return AuthCaptureResult(
            success=False,
            error="timeout",
            elapsed_seconds=time.monotonic() - start,
        )

    async def close_oauth_tab(self, url_substring: str) -> bool:
        """Close the first tab whose URL contains ``url_substring``.

        Searches both Steam CEF and Edge CDP endpoints in order;
        returns on the first close that succeeds.
        """
        endpoints = (
            (self._list_targets, self._cdp.close_target, "CEF"),
            (self._list_edge_targets, self._close_edge_target, "Edge"),
        )
        for list_fn, close_fn, label in endpoints:
            if await self._try_close_in_endpoint(
                list_fn, close_fn, url_substring, label,
            ):
                return True
        return False

    async def clear_store_cookies(self, domain: str) -> bool:
        """Clear all cookies for ``domain`` via injected JavaScript.

        SECURITY: ``domain`` is validated against a strict regex
        before being interpolated into the JS template. Without
        this check, a malicious caller could inject arbitrary
        JavaScript into Steam's CEF process.
        """
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9.\-]*$", domain):
            logger.warning(
                "[auth/browser] rejected invalid cookie domain: %r", domain,
            )
            return False
        try:
            await self._cdp.eval_js(
                "document.cookie.split(';').forEach(c => "
                "document.cookie = c.replace(/^ +/, '')"
                ".replace(/=.*/,"
                f" '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;"
                f"path=/;domain={domain}'));",
            )
            return True
        except Exception as e:
            logger.debug("[auth/browser] cookie clear failed: %s", e)
            return False

    # ── Internal: polling + capture ────────────────────────────

    async def _poll_both_endpoints(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """List CDP targets from both Steam CEF and Edge.

        Each endpoint is queried independently; failure on
        one endpoint logs at DEBUG and returns an empty
        list rather than aborting the whole poll cycle.
        Returns ``(cef_targets, edge_targets)`` so the caller
        can both concatenate them and log the per-endpoint
        counts in the heartbeat.
        """
        cef_targets: list[dict[str, Any]] = []
        edge_targets: list[dict[str, Any]] = []
        try:
            cef_targets = await self._list_targets()
        except Exception as e:  # noqa: BLE001
            logger.debug("[auth/browser] CEF target list: %s", e)
        try:
            edge_targets = await self._list_edge_targets()
        except Exception as e:  # noqa: BLE001
            logger.debug("[auth/browser] Edge target list: %s", e)
        return cef_targets, edge_targets

    @staticmethod
    def _log_heartbeat_if_due(
        state: dict[str, Any],
        cef_targets: list[dict[str, Any]],
        edge_targets: list[dict[str, Any]],
        all_targets: list[dict[str, Any]],
    ) -> None:
        """Log target counts every 30s so operators see liveness.

        Mutates ``state["last_heartbeat"]`` on each emit.
        Without this signal a stuck auth flow looks identical
        in the logs to a flow waiting on a slow user — the
        periodic heartbeat lets ops tell them apart.
        """
        now = time.monotonic()
        if now - state["last_heartbeat"] < 30:
            return
        state["last_heartbeat"] = now
        urls = [t.get("url", "")[:80] for t in all_targets if t.get("url")]
        logger.info(
            "[auth/browser] heartbeat: %d CEF + %d Edge = "
            "%d total targets, urls=%s",
            len(cef_targets), len(edge_targets),
            len(all_targets), urls,
        )

    async def _try_capture_target(
        self,
        target: dict[str, Any],
        allowed_uris: list[str],
        state: dict[str, Any],
        start: float,
    ) -> AuthCaptureResult | None:
        """Inspect one CDP target for an OAuth code.

        Returns an ``AuthCaptureResult`` on the first match
        (strict redirect, generic code-in-URL, or Epic
        content-extraction), or ``None`` if this target is
        irrelevant / already monitored / not yet ready.

        Three capture paths, tried in order:

        1. Strict redirect-URI match against ``allowed_uris``.
        2. Generic ``?code=`` extraction from the URL — catches
           providers that redirect through intermediate URLs.
        3. Page content extraction for Epic's intermediate
           page (retried every poll tick because the page
           content changes when the user finishes signing in).
        """
        url = target.get("url", "")
        if not url or url in state["monitored_urls"]:
            return None

        # Epic's content-extraction URLs are never deduped:
        # the page body starts with the login form, then the
        # ``authorizationCode`` JSON blob appears AFTER the user
        # signs in. Dedup'ing after one failed read would miss
        # the code permanently.
        is_content_page = (
            "/id/api/redirect" in url
            or "epicgames.com" in url.lower()
        )
        if not is_content_page:
            state["monitored_urls"].add(url)

        # Broad pattern matching: inspect ANY URL containing
        # OAuth-related keywords. More reliable than strict
        # redirect-URI prefix matching — some providers redirect
        # through intermediate URLs that don't match the callback
        # prefix exactly.
        if not is_oauth_relevant_url(url):
            return None

        if url not in state["logged_urls"]:
            state["logged_urls"].add(url)
            logger.info(
                "[auth/browser] OAuth page detected: %s", url[:120],
            )

        # Epic content-page extraction (priority over URL).
        if is_content_page:
            content_result = await try_epic_content_capture(
                target, url, state, start,
            )
            if content_result is not None:
                return content_result

        # Strict redirect-URI check.
        if match_redirect(url, allowed_uris):
            return build_redirect_capture(url, start)

        # Generic code extraction from any OAuth URL.
        code_from_url = extract_code_from_url(url)
        if code_from_url:
            return build_url_code_capture(url, code_from_url, start)

        return None

    # ── Internal: close + endpoints ────────────────────────────

    async def _try_close_in_endpoint(
        self,
        list_fn: Any,
        close_fn: Any,
        url_substring: str,
        label: str,
    ) -> bool:
        """Try to close one target matching ``url_substring`` on one endpoint.

        Returns True on a successful close, False on:

        * Listing the endpoint raised (no tabs visible).
        * No target's URL contains ``url_substring``.
        * The matching target lacks an ``id`` field.
        * Close itself raised (logged at DEBUG, no retry).
        """
        try:
            targets = await list_fn()
        except Exception:  # noqa: BLE001
            return False
        for target in targets:
            if url_substring not in target.get("url", ""):
                continue
            target_id = target.get("id")
            if not target_id:
                continue
            try:
                # Edge close goes through its own helper because
                # the close URL is different (``/json/close/<id>``
                # vs CEF's protocol method). CEF goes through
                # cdp_client.
                if label == "Edge":
                    await close_fn(target_id)
                else:
                    await self._cdp.close_target(target_id)
                return True
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "[auth/browser] close on %s failed: %s",
                    label, e,
                )
                return False
        return False

    async def _close_edge_target(self, target_id: str) -> None:
        """Close a single target on the Edge CDP endpoint."""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = (
                f"http://127.0.0.1:{self._edge_cdp_port}"
                f"/json/close/{target_id}"
            )
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=2),
            ):
                pass

    async def _list_targets(self) -> list[dict[str, Any]]:
        """Wrapper around the CDP client's public target listing."""
        try:
            return await self._cdp.list_targets()
        except Exception as e:
            logger.debug("[auth/browser] list_targets failed: %s", e)
            return []

    async def _list_edge_targets(self) -> list[dict[str, Any]]:
        """List targets from the Edge CDP endpoint (auth browser).

        Edge is launched by the shortcut helper with
        ``--remote-debugging-port={self._edge_cdp_port}``. This
        polls the standard CDP HTTP JSON endpoint to get the
        list of open pages/workers. Uses aiohttp so the call
        is non-blocking and fits into the asyncio polling loop.
        """
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                url = (
                    f"http://127.0.0.1:{self._edge_cdp_port}"
                    f"/json/list"
                )
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
        except Exception as e:  # noqa: BLE001 — probe must never raise
            logger.debug(
                "[auth/browser] Edge CDP not reachable (port %s): %s",
                self._edge_cdp_port, e,
            )
            return []


# ── Legacy compatibility aliases ─────────────────────────────────
CDPOAuthMonitor = OAuthBrowserMonitor
