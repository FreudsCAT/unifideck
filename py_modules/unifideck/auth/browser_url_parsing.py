"""auth/browser_url_parsing.py — Pure URL parsing for OAuth flows.

Extracted from ``auth/browser.py`` in lot 13a (file-cap split):
collects all URL-side OAuth helpers — query/fragment parsing,
redirect-URI matching, code extraction from URL parameters, and
capture-result builders — in one stateless module.

No I/O, no CDP, no logging beyond the capture-builder ones (which
log success at INFO with the auth code redacted). Every function
here is safe to call on any string in any thread.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from unifideck.utils.config_helpers import get_cfg

from .browser_types import AuthCaptureResult

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)


def extract_oauth_params(url: str) -> dict[str, str]:
    """Parse ``url`` and return its query string as a flat dict.

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


def match_redirect(url: str, allowed_uris: Iterable[str]) -> bool:
    """Return True if ``url`` matches an allowed redirect URI.

    Strict matching: scheme must be https (or http://localhost
    for local OAuth callback servers). The scheme+netloc+path
    must start with one of the allowed prefixes.
    """
    if not url:
        return False
    parsed = urlparse(url)
    # Allow https everywhere; allow http only for localhost callbacks.
    if parsed.scheme != "https" and not (
        parsed.scheme == "http"
        and parsed.hostname in ("localhost", "127.0.0.1", "[::1]")
    ):
        return False
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
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
    """Legacy alias for backward compatibility. Delegates to ``get_cfg``."""
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


def is_oauth_relevant_url(url: str) -> bool:
    """True iff ``url`` contains any known OAuth-related keyword.

    Used to filter the CDP target list down to URLs that
    deserve full inspection (regex match, code extraction).
    Skipping unrelated tabs early keeps the polling loop
    cheap on machines with many open Steam tabs.
    """
    lowered = url.lower()
    return any(kw in lowered for kw in _OAUTH_URL_KEYWORDS)


# CEF/Chromium navigation-error page markers. When the network is
# down (DNS failure), the embedded browser lands on one of these
# instead of the login page — e.g. Steam's CEF shows ``http://error/``.
# Detecting them lets the capture loop fail fast with a network error
# rather than polling uselessly until the 300s deadline.
_NAV_ERROR_MARKERS: tuple[str, ...] = (
    "http://error/", "chrome-error://", "chrome-error:",
    "net::err", "data:text/html,chromewebdata",
)


def is_navigation_error_url(url: str) -> bool:
    """True iff ``url`` is a CEF/Chromium navigation-error page.

    Signals the browser failed to load the target (offline / DNS
    failure) rather than a normal in-flight auth page.
    """
    lowered = url.lower()
    return any(marker in lowered for marker in _NAV_ERROR_MARKERS)


def build_network_unreachable_result(start: float) -> AuthCaptureResult:
    """Construct a capture result for a persistent offline condition.

    Emitted when the browser has been stuck on navigation-error
    pages past the grace window with no OAuth-relevant target in
    sight, so the flow can fail fast instead of timing out.
    """
    elapsed = time.monotonic() - start
    logger.warning(
        "[auth/browser] network unreachable after %.1fs — "
        "browser stuck on navigation-error page(s)",
        elapsed,
    )
    return AuthCaptureResult(
        success=False,
        error="network_unreachable",
        elapsed_seconds=elapsed,
    )


def build_redirect_capture(url: str, start: float) -> AuthCaptureResult:
    """Construct a capture result for a strict redirect-URI match.

    Logs the capture with the ``code=...`` query parameter
    redacted so the auth code never leaks to logs.
    """
    elapsed = time.monotonic() - start
    params = extract_oauth_params(url)
    safe_url = re.sub(
        r"(code=[^&\s]+)", "code=***REDACTED***", url,
    )
    logger.info(
        "[auth/browser] captured redirect after %.1fs: %s",
        elapsed, safe_url,
    )
    return AuthCaptureResult(
        success=True,
        redirect_url=url,
        params=params,
        elapsed_seconds=elapsed,
    )


def build_url_code_capture(
    url: str, code: str, start: float,
) -> AuthCaptureResult:
    """Construct a capture result for a generic ``?code=`` match.

    Distinct from ``build_redirect_capture``: this path is
    taken when the URL doesn't match any prefix in
    ``allowed_uris`` but still contains a ``code=`` query
    parameter — typically an intermediate provider redirect.
    """
    elapsed = time.monotonic() - start
    logger.info(
        "[auth/browser] extracted code from URL after %.1fs", elapsed,
    )
    return AuthCaptureResult(
        success=True,
        redirect_url=url,
        params={"code": code},
        elapsed_seconds=elapsed,
    )


def extract_code_from_url(url: str) -> str | None:
    """Extract an OAuth authorization code from a URL.

    Matches the standard ``code=`` query parameter and
    store-specific patterns (``authorizationCode=`` for Epic,
    ``openid.oa2.authorization_code=`` for Amazon). Returns
    the code string or ``None``.

    Pure function — no I/O, safe to call on every URL
    in the poll loop.
    """
    if not url:
        return None
    # Epic: ``authorizationCode=`` in query string
    if "authorizationCode=" in url:
        match = re.search(r"authorizationCode=([^&\s]+)", url)
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
