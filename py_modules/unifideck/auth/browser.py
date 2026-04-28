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

from ..utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from ..cdp.cdp_client import CDPClient
    from ..config import ConfigManager


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

    def to_dict(self) -> dict[str, Any]:  # noqa: D102 — documentation pending (Sprint D)
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


# ══════════════════════════════════════════════════════════════════
# Monitor class
# ══════════════════════════════════════════════════════════════════


class OAuthBrowserMonitor:
    """Watches Steam's embedded browser for an OAuth redirect.

    Uses the shared CDPClient to list browser targets and waits
    until one of them lands on a URL matching the allowed
    redirect list. Bounded by a timeout so the user can abandon
    the auth flow without leaving the plugin hanging.
    """

    def __init__(  # noqa: D107 — class docstring documents the constructor's contract
        self,
        cdp_client: CDPClient,
        config: ConfigManager | None = None,
    ) -> None:
        self._cdp = cdp_client
        self._config = config
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
    ) -> AuthCaptureResult:
        """Block until a browser tab navigates to an allowed URI.

        Returns as soon as any tab in Steam's CEF process
        matches one of the prefixes in `allowed_uris`. If the
        timeout elapses first, returns a failure result.
        """
        deadline = time.monotonic() + (
            timeout if timeout is not None
            else self._default_timeout
        )
        start = time.monotonic()
        while time.monotonic() < deadline:
            try:
                targets = await self._list_targets()
            except Exception as e:  # noqa: BLE001
                # Intentional: a transient CDP error must not
                # abort the whole capture — we just wait and
                # retry on the next poll.
                logger.debug(
                    "[auth/browser] target list: %s", e,
                )
                await asyncio.sleep(self._poll_interval)
                continue
            for target in targets:
                url = target.get("url", "")
                if match_redirect(url, allowed_uris):
                    elapsed = time.monotonic() - start
                    params = extract_oauth_params(url)
                    logger.info(
                        "[auth/browser] captured redirect "
                        "after %.1fs", elapsed,
                    )
                    return AuthCaptureResult(
                        success=True,
                        redirect_url=url,
                        params=params,
                        elapsed_seconds=elapsed,
                    )
            await asyncio.sleep(self._poll_interval)
        return AuthCaptureResult(
            success=False,
            error="timeout",
            elapsed_seconds=time.monotonic() - start,
        )

    async def close_oauth_tab(
        self, url_substring: str,
    ) -> bool:
        """Close the first tab whose URL contains `url_substring`."""
        try:
            targets = await self._list_targets()
        except Exception:  # noqa: BLE001
            return False
        for target in targets:
            if url_substring in target.get("url", ""):
                target_id = target.get("id")
                if not target_id:
                    continue
                try:
                    await self._cdp.close_target(target_id)
                    return True
                except Exception as e:  # noqa: BLE001
                    # Intentional: close failures are
                    # logged but not fatal — the capture
                    # already succeeded.
                    logger.debug(
                        "[auth/browser] close failed: %s",
                        e,
                    )
                    return False
        return False

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
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "[auth/browser] cookie clear failed: %s", e,
            )
            return False

    async def _list_targets(self) -> list[dict[str, Any]]:
        """Wrapper around the CDP client's public target listing."""
        try:
            return await self._cdp.list_targets()
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "[auth/browser] list_targets failed: %s", e,
            )
            return []


# ── Legacy compatibility aliases ─────────────────────────────────
CDPOAuthMonitor = OAuthBrowserMonitor
