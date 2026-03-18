"""
CDP (Chrome DevTools Protocol) OAuth interception for the Microsoft connector.

Captures the OAuth authorization code by attaching to the CEF browser's
Network events via WebSocket.  Handles target switching when CEF creates
new pages during the login flow (email → password → 2FA → redirect).

This is a standalone async function with no class state — all inputs
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
    cdp_port: int = 8080,
) -> Optional[str]:
    """
    Capture the OAuth code via Network.requestWillBeSent on the MS login popup.

    When the login page navigates (email → password → 2FA → …), CEF creates
    a NEW /json target with a new webSocketDebuggerUrl but keeps the OLD
    WebSocket connection open.  This function detects newer targets and
    switches to them automatically.

    Args:
        pending_auth_url: The full OAuth URL to re-navigate to if removed=true.
        timeout: Maximum seconds to wait for the OAuth code.
        cdp_port: CDP debugging port (8080 for Steam CEF, 9222 for Chromium).

    Returns:
        The OAuth authorization code, or None on timeout/failure.
    """
    import time as _time
    try:
        import websockets
    except ImportError:
        logger.warning("[MS-net] websockets not available")
        return None

    import urllib.request as _req

    MS_LOGIN_PATTERNS = (
        "login.live.com",
        "login.microsoftonline.com",
        "account.microsoft.com",
    )

    deadline = _time.time() + timeout
    seen_ws: set = set()
    removed_count = 0

    def scan_pages():
        """Return list of (url, ws_url) for unseen MS login pages."""
        try:
            with _req.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2) as r:
                pages = json.loads(r.read().decode())
            result = []
            for page in pages:
                url    = page.get("url", "")
                ws_url = page.get("webSocketDebuggerUrl", "")
                if not ws_url or ws_url in seen_ws:
                    continue
                if "removed=true" in url:
                    seen_ws.add(ws_url)
                    continue
                if "oauth20_desktop.srf" in url and "code=" in url:
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
            break
        await asyncio.sleep(0.3)

    while _time.time() < deadline:
        pages = scan_pages()
        if not pages:
            await asyncio.sleep(0.3)
            continue

        page_url, ws_url = pages[-1]
        seen_ws.add(ws_url)
        remaining = deadline - _time.time()
        logger.info(f"[MS-net] Attaching to: {page_url[:80]} ({remaining:.0f}s left)")

        try:
            async with websockets.connect(
                ws_url, ping_interval=None, open_timeout=10
            ) as ws:
                msg_id = 1

                async def send_cmd(method, params=None):
                    nonlocal msg_id
                    await ws.send(json.dumps(
                        {"id": msg_id, "method": method, "params": params or {}}
                    ))
                    msg_id += 1

                await send_cmd("Network.enable", {})
                await asyncio.wait_for(ws.recv(), timeout=10)
                logger.info("[MS-net] Network.enable OK")

                last_scan = _time.time()

                while _time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        if _time.time() - last_scan >= 1.0:
                            last_scan = _time.time()
                            newer = scan_pages()
                            if newer:
                                logger.info(f"[MS-net] Newer target: {newer[-1][0][:60]} — switching")
                                break
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("method") != "Network.requestWillBeSent":
                        continue

                    req_url = msg.get("params", {}).get("request", {}).get("url", "")
                    if "oauth20_desktop.srf" not in req_url:
                        continue

                    logger.info(f"[MS-net] requestWillBeSent → {req_url[:120]}")

                    if "code=" in req_url:
                        from urllib.parse import urlparse, parse_qs as _pqs
                        params = _pqs(urlparse(req_url).query)
                        code = params.get("code", [None])[0]
                        if code:
                            logger.info("[MS-net] ✓ OAuth code captured")
                            return code

                    elif "removed=true" in req_url:
                        removed_count += 1
                        logger.warning(
                            f"[MS-net] removed=true (attempt {removed_count}) — "
                            "clearing cookies and re-navigating"
                        )
                        if removed_count > 3:
                            logger.error("[MS-net] removed=true persists after 3 attempts — giving up")
                            return None
                        if pending_auth_url:
                            try:
                                await send_cmd("Network.enable", {})
                                await send_cmd("Network.clearBrowserCookies", {})
                                logger.info("[MS-net] Cookies cleared")
                                await asyncio.sleep(0.3)
                                await send_cmd("Page.navigate", {"url": pending_auth_url})
                                logger.info("[MS-net] Re-navigated to auth URL")
                            except Exception as _nav_err:
                                logger.debug(f"[MS-net] Re-navigation failed: {_nav_err}")
                                break
                        else:
                            break

        except websockets.exceptions.ConnectionClosed:
            logger.info("[MS-net] WS closed — rescanning")
        except Exception as e:
            logger.debug(f"[MS-net] Listener error: {e}")

        await asyncio.sleep(0.3)

    logger.warning("[MS-net] Timed out waiting for OAuth code")
    return None
