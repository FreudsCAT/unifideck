"""
CDP (Chrome DevTools Protocol) OAuth interception for the Microsoft connector.

Captures the OAuth authorization code by attaching to Chromium's
Network events via WebSocket.  Handles target switching when Chromium
creates new pages during the login flow (email → password → 2FA → redirect).

This is a standalone async function with no class state -- all inputs
are explicit parameters.
"""

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["intercept_oauth_code"]


async def intercept_oauth_code(
    pending_auth_url: str,
    timeout: float = 300,
    cdp_port: int = 9222,
) -> Optional[str]:
    """
    Capture the OAuth code via CDP Network/Page events on the MS login popup.

    When the login page navigates (email -> password -> 2FA -> ...),
    Chromium creates a NEW /json target with a new webSocketDebuggerUrl
    but keeps the OLD WebSocket connection open.  This function detects
    newer targets and switches to them automatically.

    Args:
        pending_auth_url: The full OAuth URL to re-navigate to if removed=true.
        timeout: Maximum seconds to wait for the OAuth code.
        cdp_port: CDP debugging port (default 9222 for Chromium).

    Returns:
        The OAuth authorization code, or None on timeout/failure.
    """
    import time as _time
    try:
        import websockets
    except ImportError:
        websockets = None
        logger.warning("[MS-net] websockets not available -- falling back to target polling")

    import urllib.request as _req
    from urllib.parse import urlparse, parse_qs as _pqs

    MS_LOGIN_PATTERNS = (
        "login.live.com",
        "login.microsoftonline.com",
        "account.microsoft.com",
    )

    deadline = _time.time() + timeout
    seen_ws: set = set()
    removed_count = 0

    def _extract_code(url: str) -> Optional[str]:
        """Extract OAuth code from a redirect URL, if present."""
        if "oauth20_desktop.srf" not in url or "code=" not in url:
            return None
        params = _pqs(urlparse(url).query)
        return params.get("code", [None])[0]

    def scan_pages():
        """Return list of (url, ws_url) for unseen MS login pages."""
        try:
            with _req.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2) as r:
                targets = json.loads(r.read().decode())
            result = []
            for target in targets:
                # Only attach to page targets (skip browser, service-worker, etc.)
                if target.get("type", "page") not in ("page", "webview"):
                    continue
                url    = target.get("url", "")
                ws_url = target.get("webSocketDebuggerUrl", "")

                # Check if this target already has the code in its URL
                code = _extract_code(url)
                if code:
                    if ws_url:
                        seen_ws.add(ws_url)
                    return [("__CODE__", code)]

                if not ws_url:
                    continue
                if ws_url in seen_ws:
                    continue
                if "removed=true" in url:
                    seen_ws.add(ws_url)
                    continue
                if any(p in url for p in MS_LOGIN_PATTERNS):
                    result.append((url, ws_url))
            return result
        except Exception as e:
            logger.debug(f"[MS-net] scan error: {e}")
            return []

    logger.info("[MS-net] Starting Network interception with live target tracking")

    # Wait for first MS page to appear
    while _time.time() < deadline:
        pages = scan_pages()
        if pages:
            # Check if scan already found the code in a target URL
            if pages[0][0] == "__CODE__":
                logger.info("[MS-net] OAuth code found in target URL during scan")
                return pages[0][1]
            break
        await asyncio.sleep(0.3)

    while _time.time() < deadline:
        pages = scan_pages()
        if not pages:
            await asyncio.sleep(0.3)
            continue

        # Check if scan already found the code in a target URL
        if pages[0][0] == "__CODE__":
            logger.info("[MS-net] OAuth code found in target URL during scan")
            return pages[0][1]

        page_url, ws_url = pages[-1]
        seen_ws.add(ws_url)
        remaining = deadline - _time.time()
        logger.info(f"[MS-net] Attaching to: {page_url[:80]} ({remaining:.0f}s left)")

        if websockets is None:
            await asyncio.sleep(0.3)
            continue

        try:
            async with websockets.connect(
                ws_url, ping_interval=None, open_timeout=10
            ) as ws:
                msg_id = 1

                async def send_cmd(method, params=None):
                    nonlocal msg_id
                    cmd = {"id": msg_id, "method": method, "params": params or {}}
                    await ws.send(json.dumps(cmd))
                    msg_id += 1

                # Enable both Network and Page domains for comprehensive
                # event coverage. Don't block on responses -- they arrive
                # asynchronously and the event loop below handles them.
                await send_cmd("Network.enable", {})
                await send_cmd("Page.enable", {})
                logger.info("[MS-net] Network.enable + Page.enable sent")

                last_scan = _time.time()

                while _time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        if _time.time() - last_scan >= 1.0:
                            last_scan = _time.time()
                            newer = scan_pages()
                            if newer:
                                if newer[0][0] == "__CODE__":
                                    logger.info("[MS-net] OAuth code found in target URL during rescan")
                                    return newer[0][1]
                                logger.info(f"[MS-net] Newer target: {newer[-1][0][:60]} -- switching")
                                break
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # Skip CDP command responses (have "id" but no "method")
                    method = msg.get("method", "")
                    if not method:
                        continue

                    # Extract URL from the event based on its type
                    req_url = ""
                    if method == "Network.requestWillBeSent":
                        req_url = msg.get("params", {}).get("request", {}).get("url", "")
                    elif method == "Network.responseReceived":
                        req_url = msg.get("params", {}).get("response", {}).get("url", "")
                    elif method == "Page.frameNavigated":
                        req_url = msg.get("params", {}).get("frame", {}).get("url", "")
                    else:
                        continue

                    if "oauth20_desktop.srf" not in req_url:
                        continue

                    logger.info(f"[MS-net] {method} -> {req_url[:120]}")

                    if "code=" in req_url:
                        code = _extract_code(req_url)
                        if code:
                            logger.info("[MS-net] OAuth code captured")
                            return code

                    elif "removed=true" in req_url:
                        removed_count += 1
                        logger.warning(
                            f"[MS-net] removed=true (attempt {removed_count}/3) -- "
                            "possible cookie or account issue, clearing and retrying"
                        )
                        if removed_count > 3:
                            logger.error(
                                "[MS-net] removed=true persists after 3 attempts. "
                                "Possible causes: account rejected, device blocked, "
                                "or session timeout. Try clearing browser cache."
                            )
                            return None
                        if pending_auth_url:
                            try:
                                backoff = min(2 ** (removed_count - 1), 8)
                                await asyncio.sleep(backoff)
                                await send_cmd("Network.clearBrowserCookies", {})
                                logger.info("[MS-net] Cookies cleared")
                                await asyncio.sleep(0.5)
                                await send_cmd("Page.navigate", {"url": pending_auth_url})
                                logger.info("[MS-net] Re-navigated to auth URL")
                            except Exception as _nav_err:
                                logger.error(f"[MS-net] Re-navigation failed: {_nav_err}")
                                return None
                        else:
                            logger.error("[MS-net] removed=true but no pending auth URL")
                            return None

        except Exception as e:
            if "ConnectionClosed" in type(e).__name__:
                logger.info("[MS-net] WS closed -- rescanning")
            else:
                logger.debug(f"[MS-net] Listener error: {e}")

        await asyncio.sleep(0.3)

    logger.warning("[MS-net] Timed out waiting for OAuth code")
    return None
