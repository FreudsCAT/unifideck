"""
Generic CDP OAuth interceptor for Unifideck auth flows.

Captures OAuth authorization codes by attaching to Chromium's
Network/Page events via WebSocket on a configurable CDP port.
Handles target switching when Chromium creates new pages during
multi-step login flows (email → password → 2FA → redirect).

Used by Epic, GOG, and Amazon auth flows (each launching Edge
on port 9222 via unifideck-launcher). Microsoft has its own
specialized interceptor in microsoft_cdp.py.
"""

import asyncio
import json
import logging
import re
import time
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

__all__ = ["intercept_oauth_code"]


# ── Store-specific code extraction ──────────────────────────────────────


def _extract_epic_code(url: str) -> Optional[str]:
    """Extract Epic authorization code from redirect URL."""
    if "authorizationCode=" in url:
        m = re.search(r"authorizationCode=([^&\s]+)", url)
        return m.group(1) if m else None
    return None


def _extract_gog_code(url: str) -> Optional[str]:
    """Extract GOG authorization code from redirect URL."""
    if "code=" in url:
        params = parse_qs(urlparse(url).query)
        codes = params.get("code")
        return codes[0] if codes else None
    return None


def _extract_amazon_code(url: str) -> Optional[str]:
    """Extract Amazon authorization code from redirect URL."""
    if "openid.oa2.authorization_code=" in url:
        m = re.search(r"openid\.oa2\.authorization_code=([^&\s]+)", url)
        return m.group(1) if m else None
    return None


# Map store names to their extraction functions and login page patterns
STORE_CONFIGS: Dict[str, dict] = {
    "epic": {
        "extract": _extract_epic_code,
        "login_patterns": ("epicgames.com", "unrealengine.com"),
        "redirect_markers": ("authorizationCode=", "/id/api/redirect"),
    },
    "gog": {
        "extract": _extract_gog_code,
        "login_patterns": ("gog.com", "auth.gog.com"),
        "redirect_markers": ("on_login_success", "code="),
    },
    "amazon": {
        "extract": _extract_amazon_code,
        "login_patterns": ("amazon.com", "amazon.co"),
        "redirect_markers": ("openid.oa2.authorization_code=",),
    },
}


# ── Epic page-body fallback ────────────────────────────────────────────


async def _extract_epic_code_from_page(
    ws_url: str, cdp_port: int, tag: str
) -> Optional[str]:
    """
    Fallback for Epic: read ``document.body.innerText`` via
    ``Runtime.evaluate`` and search for the JSON-embedded
    ``authorizationCode`` field.
    """
    try:
        import websockets
    except ImportError:
        return None

    try:
        async with websockets.connect(
            ws_url, ping_interval=None, open_timeout=5
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": "document.body.innerText",
                            "returnByValue": True,
                        },
                    }
                )
            )
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(raw)
            if "result" in data and "result" in data["result"]:
                body = data["result"]["result"].get("value", "")
                m = re.search(r'"authorizationCode"\s*:\s*"([^"]+)"', body)
                if m:
                    logger.info(f"{tag} Extracted authorizationCode from page body")
                    return m.group(1)
    except Exception as e:
        logger.debug(f"{tag} Epic page-body fallback error: {e}")
    return None


# ── Public entry point ──────────────────────────────────────────────────


async def intercept_oauth_code(
    store: str,
    timeout: float = 300,
    cdp_port: int = 9222,
) -> Optional[str]:
    """
    Generic OAuth code interceptor via CDP Network events.

    Attaches to Chromium on the given CDP port, monitors page
    navigations and network requests for OAuth redirect URLs,
    and extracts the authorization code.

    Args:
        store:    Store identifier (``'epic'``, ``'gog'``, ``'amazon'``).
        timeout:  Maximum seconds to wait for the OAuth code.
        cdp_port: CDP debugging port (default 9222).

    Returns:
        The OAuth authorization code, or ``None`` on timeout/failure.
    """
    config = STORE_CONFIGS.get(store)
    if config is None:
        logger.error(f"[CDP-{store}] Unknown store '{store}'")
        return None

    tag = f"[CDP-{store}]"
    extract_fn = config["extract"]
    redirect_markers: Tuple[str, ...] = tuple(config["redirect_markers"])

    try:
        import websockets
    except ImportError:
        websockets = None  # type: ignore[assignment]
        logger.warning(f"{tag} websockets not available -- falling back to target polling")

    import urllib.request as _req

    deadline = time.time() + timeout
    seen_ws: set = set()

    # ── helpers ──────────────────────────────────────────────────────

    def _scan_pages():
        """
        Poll ``/json`` for page/webview targets.

        Since we launch a dedicated Chromium instance for this auth
        flow, every page target is potentially relevant (not just
        those matching login_patterns).  We still check each target's
        URL for a code, and return any unseen page targets for WS
        attachment.
        """
        try:
            with _req.urlopen(
                f"http://127.0.0.1:{cdp_port}/json", timeout=2
            ) as r:
                targets = json.loads(r.read().decode())
            result = []
            for target in targets:
                if target.get("type", "page") not in ("page", "webview"):
                    continue
                url = target.get("url", "")
                ws_url = target.get("webSocketDebuggerUrl", "")

                # Check if this target already has the code in its URL
                code = extract_fn(url)
                if code:
                    if ws_url:
                        seen_ws.add(ws_url)
                    return [("__CODE__", code, ws_url)]

                if not ws_url:
                    continue
                if ws_url in seen_ws:
                    continue

                result.append((url, ws_url))
            return result
        except Exception as e:
            logger.debug(f"{tag} scan error: {e}")
            return []

    # ── phase 1: wait for at least one page target ──────────────────

    logger.info(f"{tag} Starting Network interception with live target tracking")

    while time.time() < deadline:
        pages = _scan_pages()
        if pages:
            if pages[0][0] == "__CODE__":
                logger.info(f"{tag} OAuth code found in target URL during initial scan")
                return pages[0][1]
            break
        await asyncio.sleep(0.3)

    # ── phase 2: attach WS, listen for events, switch targets ───────

    while time.time() < deadline:
        pages = _scan_pages()
        if not pages:
            await asyncio.sleep(0.3)
            continue

        if pages[0][0] == "__CODE__":
            logger.info(f"{tag} OAuth code found in target URL during scan")
            return pages[0][1]

        page_url, ws_url = pages[-1]
        seen_ws.add(ws_url)
        remaining = deadline - time.time()
        logger.info(f"{tag} Attaching to: {page_url[:80]} ({remaining:.0f}s left)")

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

                await send_cmd("Network.enable", {})
                await send_cmd("Page.enable", {})
                logger.info(f"{tag} Network.enable + Page.enable sent")

                last_scan = time.time()

                while time.time() < deadline:
                    # ── read one WS message (1 s timeout) ───────────
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        # Periodically rescan for newer targets
                        if time.time() - last_scan >= 1.0:
                            last_scan = time.time()
                            newer = _scan_pages()
                            if newer:
                                if newer[0][0] == "__CODE__":
                                    logger.info(
                                        f"{tag} OAuth code found in target URL during rescan"
                                    )
                                    return newer[0][1]
                                logger.info(
                                    f"{tag} Newer target: {newer[-1][0][:60]} -- switching"
                                )
                                break  # break inner loop to re-attach
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    method = msg.get("method", "")
                    if not method:
                        continue

                    # Extract URL from the CDP event
                    req_url = ""
                    if method == "Network.requestWillBeSent":
                        req_url = (
                            msg.get("params", {}).get("request", {}).get("url", "")
                        )
                    elif method == "Network.responseReceived":
                        req_url = (
                            msg.get("params", {}).get("response", {}).get("url", "")
                        )
                    elif method == "Page.frameNavigated":
                        req_url = (
                            msg.get("params", {}).get("frame", {}).get("url", "")
                        )
                    else:
                        continue

                    if not req_url:
                        continue

                    # Quick check: does the URL contain any redirect marker?
                    if not any(m in req_url for m in redirect_markers):
                        continue

                    logger.info(f"{tag} {method} -> {req_url[:120]}")

                    code = extract_fn(req_url)
                    if code:
                        logger.info(f"{tag} OAuth code captured")
                        return code

                    # Epic fallback: code might be in page body, not URL
                    if store == "epic" and "/id/api/redirect" in req_url:
                        logger.info(
                            f"{tag} Epic redirect detected -- trying page body fallback"
                        )
                        body_code = await _extract_epic_code_from_page(
                            ws_url, cdp_port, tag
                        )
                        if body_code:
                            return body_code

        except Exception as e:
            if "ConnectionClosed" in type(e).__name__:
                logger.info(f"{tag} WS closed -- rescanning")
            else:
                logger.debug(f"{tag} Listener error: {e}")

        await asyncio.sleep(0.3)

    logger.warning(f"{tag} Timed out waiting for OAuth code")
    return None
