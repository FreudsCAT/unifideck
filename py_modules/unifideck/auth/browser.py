"""auth/browser.py — OAuth code capture via CDP page monitoring.

While the user logs in to a store via the in-Steam embedded
browser, this module watches the browser's tab list (via CDP)
and captures the authorization code from the redirect URL the
moment it appears.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.cdp.cdp_client import CDPClient
    from unifideck.config import ConfigManager


logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 0.5  # seconds between tab list checks
DEFAULT_OAUTH_TIMEOUT = 300  # seconds


# ══════════════════════════════════════════════════════════════════
# Result types
# ══════════════════════════════════════════════════════════════════


@dataclass
class AuthCaptureResult:
    """Outcome of an OAuth redirect capture attempt."""

    success: bool
    redirect_url: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    error: str | None = None

    @property
    def code(self) -> str | None:
        """Convenience: return the `code` query parameter if any."""
        return self.params.get("code")

    @property
    def state(self) -> str | None:
        """Convenience: return the `state` query parameter if any."""
        return self.params.get("state")

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "redirect_url": self.redirect_url,
            "params": dict(self.params),
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
        }


# ══════════════════════════════════════════════════════════════════
# Pure helpers
# ══════════════════════════════════════════════════════════════════


def extract_oauth_params(url: str) -> dict[str, str]:
    """Parse `url` and return its query string as a flat dict.

    Pure function — no I/O. Multi-value parameters are flattened
    to their first value (OAuth spec only has one of each).
    Fragment-encoded params (implicit flow) are also extracted.
    """
    if not url:
        return {}
    parsed = urlparse(url)
    out: dict[str, str] = {}
    # Query string
    for key, values in parse_qs(parsed.query).items():
        if values:
            out[key] = values[0]
    # Fragment (implicit flow)
    if parsed.fragment:
        for key, values in parse_qs(parsed.fragment).items():
            if values and key not in out:
                out[key] = values[0]
    return out


def match_redirect(
    url: str, allowed_uris: Iterable[str],
) -> bool:
    """Return True if `url` matches an allowed redirect URI.

    Strict matching: scheme must be https (or http://localhost
    for local OAuth callback servers). The scheme+netloc+path
    must start with one of the allowed prefixes.
    """
    if not url:
        return False
    parsed = urlparse(url)
    # Allow https everywhere; allow http only for localhost
    # callbacks.
    if parsed.scheme != "https" and not (
        parsed.scheme == "http"
        and parsed.hostname in (
            "localhost", "127.0.0.1", "[::1]",
        )
    ):
        return False
    base = (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    )
    for prefix in allowed_uris:
        if not prefix:
            continue
        prefix_parsed = urlparse(prefix)
        prefix_base = (
            f"{prefix_parsed.scheme}://"
            f"{prefix_parsed.netloc}{prefix_parsed.path}"
        )
        if base.startswith(prefix_base):
            return True
    return False


def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:
    """Legacy alias for backward compatibility. Delegates to `get_cfg`."""
    return get_cfg(config, key, default)


# OAuth-related URL keywords used to gate which targets are
# worth full inspection. Tuple (not set) because order of
# checks doesn't matter and a tuple is the natural literal for
# a fixed sentinel list — also slightly faster for tiny N.
_OAUTH_URL_KEYWORDS: tuple[str, ...] = (
    "auth", "login", "code=", "epiclogin",
    "on_login_success", "oauth", "authorizationcode",
    "/id/api/redirect", "oauth20_desktop.srf",
    "live.com/oauth", "callback", "maplanding",
)


def _is_oauth_relevant_url(url: str) -> bool:
    """True iff ``url`` contains any known OAuth-related keyword.

    Used to filter the CDP target list down to URLs that
    deserve full inspection (regex match, code extraction).
    Skipping unrelated tabs early keeps the polling loop
    cheap on machines with many open Steam tabs.
    """
    lowered = url.lower()
    return any(kw in lowered for kw in _OAUTH_URL_KEYWORDS)


def _build_redirect_capture(
    url: str, start: float,
) -> AuthCaptureResult:
    """Construct a capture result for a strict redirect-URI match.

    Logs the capture with the ``code=...`` query parameter
    redacted so the auth code never leaks to logs.
    """
    elapsed = time.monotonic() - start
    params = extract_oauth_params(url)
    safe_url = re.sub(
        r'(code=[^&\s]+)', 'code=***REDACTED***', url,
    )
    logger.info(
        "[auth/browser] captured redirect "
        "after %.1fs: %s", elapsed, safe_url,
    )
    return AuthCaptureResult(
        success=True,
        redirect_url=url,
        params=params,
        elapsed_seconds=elapsed,
    )


def _build_url_code_capture(
    url: str, code: str, start: float,
) -> AuthCaptureResult:
    """Construct a capture result for a generic ``?code=`` match.

    Distinct from ``_build_redirect_capture``: this path is
    taken when the URL doesn't match any prefix in
    ``allowed_uris`` but still contains a ``code=`` query
    parameter — typically an intermediate provider redirect.
    """
    elapsed = time.monotonic() - start
    logger.info(
        "[auth/browser] extracted code from URL "
        "after %.1fs", elapsed,
    )
    return AuthCaptureResult(
        success=True,
        redirect_url=url,
        params={"code": code},
        elapsed_seconds=elapsed,
    )


def _log_extract(first_attempt: bool, fmt: str, *args: Any) -> None:
    """Log content-extraction events at INFO on the first attempt, DEBUG after.

    The page body for Epic's ``/id/api/redirect`` mutates
    during login — first checks always fail (login form,
    no code yet), so failure on the first attempt is
    diagnostic. Subsequent retries are routine polling
    chatter and belong at DEBUG.
    """
    level = logger.info if first_attempt else logger.debug
    level("[auth/browser] " + fmt, *args)


def _match_pattern_in_text(
    text: str,
    pattern: str,
    url_snippet: str,
    first_attempt: bool,
) -> str | None:
    """Apply ``pattern`` to ``text``, return the first capture group.

    Logs a miss at INFO/DEBUG (per ``first_attempt``)
    including the body length so operators can tell
    "page loaded but no code yet" from "page never
    loaded at all".
    """
    match = re.search(pattern, text)
    if match:
        return match.group(1)
    _log_extract(
        first_attempt,
        "pattern not found in page content (%d chars) for %s",
        len(text), url_snippet,
    )
    return None


# ══════════════════════════════════════════════════════════════════
# Monitor class
# ══════════════════════════════════════════════════════════════════


class OAuthBrowserMonitor:
    """Watches Steam's embedded browser and Edge for an OAuth redirect.

    Polls both the Steam CEF CDP endpoint (port 8080) and the
    Edge CDP endpoint (port 9222) every `poll_interval` seconds
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

        Returns as soon as any tab in Steam's CEF process
        **or** the Edge auth browser matches one of the
        prefixes in `allowed_uris`. If the timeout elapses
        first, returns a failure result.

        When ``content_trigger_url`` and ``content_regex``
        are both set, *also* scans page content for a
        matching group: if any target's URL contains
        ``content_trigger_url``, the method connects to
        that target via CDP, evaluates
        ``document.body.innerText``, and applies the regex.
        This is used as a fallback for stores (e.g. Epic)
        that embed the authorization code in a JSON blob
        inside the page body rather than a query parameter.

        Refactor history (lot 13a): originally a single
        method at cognitive complexity 63, decomposed
        into 4 private helpers — ``_poll_both_endpoints``,
        ``_log_heartbeat_if_due``, ``_try_capture_target``,
        ``_try_content_fallback`` — so this orchestrator
        stays under the CCR=15 gate.
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

            fallback = await self._try_content_fallback(
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

    async def _poll_both_endpoints(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """List CDP targets from both Steam CEF and Edge.

        Each endpoint is queried independently; failure on
        one endpoint logs at DEBUG and returns an empty
        list rather than aborting the whole poll cycle.
        Returns ``(cef_targets, edge_targets)`` so the
        caller can both concatenate them and log the
        per-endpoint counts in the heartbeat.
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
           This is the standard OAuth callback path with the
           code in the query string.
        2. Generic ``?code=`` extraction from the URL — catches
           providers that redirect through intermediate URLs
           that don't match the callback prefix exactly.
        3. Page content extraction for Epic's intermediate
           page (``/id/api/redirect`` with the JSON blob
           containing ``authorizationCode``). Retried every
           poll tick because the page content changes when
           the user finishes signing in.

        Returns:
            ``AuthCaptureResult`` on capture, or ``None``
            when the target doesn't (yet) yield a code.
        """
        url = target.get("url", "")
        if not url or url in state["monitored_urls"]:
            return None

        # Epic's content-extraction URLs are never deduped:
        # the page body starts with the login form, then the
        # ``authorizationCode`` JSON blob appears AFTER the
        # user signs in. Dedup'ing after one failed read
        # would miss the code permanently.
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
        if not _is_oauth_relevant_url(url):
            return None

        if url not in state["logged_urls"]:
            state["logged_urls"].add(url)
            logger.info(
                "[auth/browser] OAuth page detected: %s",
                url[:120],
            )

        # Epic content-page extraction (priority over URL).
        if is_content_page:
            content_result = await self._try_epic_content_capture(
                target, url, state, start,
            )
            if content_result is not None:
                return content_result

        # Strict redirect-URI check.
        if match_redirect(url, allowed_uris):
            return _build_redirect_capture(url, start)

        # Generic code extraction from any OAuth URL.
        code_from_url = self._extract_code(url)
        if code_from_url:
            return _build_url_code_capture(url, code_from_url, start)

        return None

    async def _try_epic_content_capture(
        self,
        target: dict[str, Any],
        url: str,
        state: dict[str, Any],
        start: float,
    ) -> AuthCaptureResult | None:
        """Run the Epic-specific JSON-blob extraction on a target.

        Updates ``state["content_extract_first_attempt"]``
        so log verbosity is correctly escalated only on
        the first attempt per URL. Returns the capture
        result on success, ``None`` on extraction failure
        (silent — caller retries next poll tick).
        """
        first = url not in state["content_extract_first_attempt"]
        try:
            code = await self._extract_code_from_page(
                target,
                r'"authorizationCode"\s*:\s*"([^"]+)"',
                first_attempt=first,
            )
        except Exception:  # noqa: BLE001
            code = None
        state["content_extract_first_attempt"].add(url)
        if not code:
            return None
        elapsed = time.monotonic() - start
        logger.info(
            "[auth/browser] extracted Epic code "
            "from page content after %.1fs", elapsed,
        )
        return AuthCaptureResult(
            success=True,
            redirect_url=url,
            params={"code": code},
            elapsed_seconds=elapsed,
        )

    async def _try_content_fallback(
        self,
        targets: list[dict[str, Any]],
        content_trigger_url: str | None,
        content_regex: str | None,
        start: float,
    ) -> AuthCaptureResult | None:
        """Apply the optional caller-supplied content-extraction pattern.

        Distinct from ``_try_epic_content_capture`` which
        is hardcoded for Epic's URL + regex. This one is
        the generic mechanism — used by stores that pass
        their own ``content_trigger_url`` + ``content_regex``
        as kwargs to ``wait_for_redirect``.
        """
        if not (content_trigger_url and content_regex):
            return None
        for target in targets:
            if content_trigger_url not in target.get("url", ""):
                continue
            try:
                code = await self._extract_code_from_page(
                    target, content_regex,
                )
            except Exception:  # noqa: BLE001
                continue
            if not code:
                continue
            elapsed = time.monotonic() - start
            logger.info(
                "[auth/browser] extracted code from page "
                "content after %.1fs", elapsed,
            )
            return AuthCaptureResult(
                success=True,
                redirect_url=target.get("url"),
                params={"code": code},
                elapsed_seconds=elapsed,
            )
        return None

    async def close_oauth_tab(self, url_substring: str) -> bool:
        """Close the first tab whose URL contains ``url_substring``.

        Searches both Steam CEF and Edge CDP endpoints in
        order; returns on the first close that succeeds.

        Refactor history (lot 13a): the original method
        had a double-nested ``for endpoint / for target``
        loop with three failure paths (list, target_id
        missing, close) at cognitive complexity 21. The
        per-endpoint logic is now extracted into
        ``_try_close_in_endpoint``.
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

    async def _try_close_in_endpoint(
        self,
        list_fn: Any,
        close_fn: Any,
        url_substring: str,
        label: str,
    ) -> bool:
        """Try to close one target matching ``url_substring`` on one endpoint.

        Returns True on a successful close, False on:

        * Listing the endpoint raised (no tabs visible — try
          the next endpoint).
        * No target's URL contains ``url_substring`` (the tab
          isn't on this endpoint).
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
                # Edge close goes through its own helper
                # because the close URL is different
                # (``/json/close/<id>`` vs CEF's protocol
                # method). CEF goes through cdp_client.
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

    async def _extract_code_from_page(
        self,
        target: dict[str, Any],
        pattern: str,
        *,
        first_attempt: bool = False,
    ) -> str | None:
        """Connect to a CDP target and regex page body for an auth code.

        Used as a fallback when URL-param extraction fails
        (e.g. Epic embeds ``authorizationCode`` in a JSON blob
        inside the intermediate ``/id/api/redirect`` page body).

        Connects via CDP websocket, sends ``Runtime.evaluate``
        with ``document.body.innerText``, and applies
        ``pattern`` to the returned text.

        Refactor history (lot 13a): originally a 90-line
        method at cognitive complexity 17 with four interleaved
        log-or-debug branches (timeout, missing socket, empty
        body, regex miss, generic exception). Decomposed into
        ``_cdp_eval_inner_text`` for the protocol-level work
        and ``_match_pattern_in_text`` for the regex side, with
        ``_log_extract`` centralising the first-attempt vs
        subsequent-attempt log level rule.

        Args:
            target: CDP target dict with ``webSocketDebuggerUrl``.
            pattern: regex whose first capture group is the
                authorization code.
            first_attempt: if True, log failure reasons at INFO
                so operators can diagnose extraction issues.
        """
        ws_url = target.get("webSocketDebuggerUrl")
        url_snippet = target.get("url", "")[:80]
        if not ws_url:
            _log_extract(
                first_attempt,
                "content extract skipped: no webSocketDebuggerUrl for %s",
                url_snippet,
            )
            return None
        try:
            text = await self._cdp_eval_inner_text(ws_url, url_snippet, first_attempt)
        except Exception as exc:  # noqa: BLE001
            _log_extract(
                first_attempt,
                "content extract failed for %s: %s",
                url_snippet, exc,
            )
            return None
        if text is None:
            return None
        return _match_pattern_in_text(text, pattern, url_snippet, first_attempt)

    @staticmethod
    async def _cdp_eval_inner_text(
        ws_url: str,
        url_snippet: str,
        first_attempt: bool,
    ) -> str | None:
        """Send ``Runtime.evaluate(document.body.innerText)`` and parse.

        Returns the evaluated string on success. Returns
        ``None`` (and logs at the appropriate level) when:

        * The websocket recv times out after 5 s.
        * The CDP response has no ``result.result.value`` chain.

        Raises on connection / serialisation errors — the
        caller wraps in a broad except to demote those to
        DEBUG since they're routine during browser teardown.
        """
        import json as _json

        import websockets as _websockets

        async with _websockets.connect(
            ws_url, ping_interval=None,
        ) as ws:
            await ws.send(_json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": "document.body?.innerText || ''",
                    "returnByValue": True,
                },
            }))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except TimeoutError:
                _log_extract(
                    first_attempt,
                    "content extract timeout for %s",
                    url_snippet,
                )
                return None
        data = _json.loads(raw)
        # CDP response shape:
        #  { "id": 1, "result": { "result": { "value": "..." } } }
        value = (
            data.get("result", {})
            .get("result", {})
            .get("value", "")
        )
        if not value:
            _log_extract(
                first_attempt,
                "empty page content for %s",
                url_snippet,
            )
            return None
        return str(value)

    async def clear_store_cookies(self, domain: str) -> bool:
        """Clear all cookies for `domain` via injected JavaScript.

        SECURITY: `domain` is validated against a strict regex
        before being interpolated into the JS template. Without
        this check, a malicious caller could inject arbitrary
        JavaScript into Steam's CEF process.
        """
        if not re.match(
            r"^[a-zA-Z0-9][a-zA-Z0-9.\-]*$", domain,
        ):
            logger.warning(
                "[auth/browser] rejected invalid cookie "
                "domain: %r", domain,
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
            logger.debug(
                "[auth/browser] cookie clear failed: %s", e,
            )
            return False

    async def _list_targets(self) -> list[dict[str, Any]]:
        """Wrapper around the CDP client's public target listing."""
        try:
            return await self._cdp.list_targets()
        except Exception as e:
            logger.debug(
                "[auth/browser] list_targets failed: %s", e,
            )
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
            # Log the first failure at INFO so we know the
            # Edge CDP endpoint isn't up yet (normal during
            # the browser-launch grace period). After the
            # first, suppress to DEBUG to avoid log spam.
            logger.debug(
                "[auth/browser] Edge CDP not reachable "
                "(port %s): %s", self._edge_cdp_port, e,
            )
            return []

    @staticmethod
    def _extract_code(url: str) -> str | None:
        """Extract an OAuth authorization code from a URL.

        Matches the standard ``code=`` query parameter and
        store-specific patterns (authorizationCode= for Epic,
        openid.oa2.authorization_code= for Amazon). Returns
        the code string or ``None``.

        Pure function — no I/O, safe to call on every URL
        in the poll loop.
        """
        if not url:
            return None
        # Epic: ``authorizationCode=`` in query string
        if "authorizationCode=" in url:
            match = re.search(
                r"authorizationCode=([^&\s]+)", url,
            )
            if match:
                return match.group(1)
        # Amazon: ``openid.oa2.authorization_code=``
        if "openid.oa2.authorization_code=" in url.lower():
            match = re.search(
                r"openid\.oa2\.authorization_code=([^&\s]+)", url,
            )
            if match:
                return match.group(1)
        # Standard OAuth: ``code=`` query parameter
        if "code=" in url:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            code_list = params.get("code")
            if code_list:
                return code_list[0]
        return None


# ── Legacy compatibility aliases ─────────────────────────────────
CDPOAuthMonitor = OAuthBrowserMonitor
