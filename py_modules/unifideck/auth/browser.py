"""
Chrome DevTools Protocol (CDP) based OAuth monitor.

This module provides tools for monitoring Steam's CEF browser for OAuth 
authorization codes from Epic, GOG, and Amazon logins.
"""
import asyncio
import json
import logging
import re
import urllib.request
from urllib.parse import urlparse, parse_qs
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class CDPOAuthMonitor:
    """Monitor Steam's CEF browser for OAuth authorization codes via Chrome DevTools Protocol"""

    def __init__(self, cef_port=8080):
        self.cef_url = f'http://127.0.0.1:{cef_port}/json'
        self.monitored_urls = set()

    async def monitor_for_oauth_code(self, expected_store='epic', timeout=300, poll_interval=0.5) -> Tuple[Optional[str], Optional[str]]:
        """
        Monitor CEF pages for OAuth redirect URLs and extract authorization codes.

        For Microsoft, two strategies run concurrently:
          - URL polling   : catches the redirect if the page is visible in /json
          - CDP events    : listens to Page.frameNavigated + Network.requestWillBeSent on
                            the login page, catching the redirect the instant it is issued
                            even if the popup closes before the next poll tick.

        Args:
            expected_store: Only return codes for this store ('epic', 'gog', 'amazon', 'microsoft')
            timeout: Maximum time to monitor in seconds (default 5 minutes)
            poll_interval: How often to check CEF pages in seconds (default 0.5s)

        Returns:
            (code, store) tuple or (None, None) if timeout/error
        """
        logger.info(f"[CDP] Starting OAuth code monitoring for {expected_store}...")

        if expected_store == 'microsoft':
            # Run URL-poll and event-listener concurrently; return whichever wins.
            poll_task   = asyncio.create_task(self._poll_for_code(expected_store, timeout, poll_interval))
            events_task = asyncio.create_task(self._monitor_ms_page_events(timeout))

            done, pending = await asyncio.wait(
                {poll_task, events_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            result = done.pop().result()
            if result and result[0]:
                return result
            # Both timed out / returned nothing
            logger.warning("[CDP] OAuth monitoring timeout - no Microsoft code found")
            return None, None

        # Non-Microsoft stores: existing poll-only path
        return await self._poll_for_code(expected_store, timeout, poll_interval)

    # ── internal: URL polling ────────────────────────────────────────────────

    async def _poll_for_code(self, expected_store: str, timeout: float, poll_interval: float) -> Tuple[Optional[str], Optional[str]]:
        """Poll /json every poll_interval seconds looking for OAuth redirect URLs."""
        import time
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                with urllib.request.urlopen(self.cef_url, timeout=2) as response:
                    pages = json.loads(response.read().decode())

                for page in pages:
                    url = page.get('url', '')

                    if url in self.monitored_urls:
                        continue
                    self.monitored_urls.add(url)

                    if any(p in url.lower() for p in [
                        'auth', 'login', 'code=', 'epiclogin', 'on_login_success',
                        'oauth', 'authorizationcode', '/id/api/redirect',
                        'oauth20_desktop.srf', 'live.com/oauth',
                    ]):
                        logger.info(f"[CDP] OAuth page detected: {url[:80]}...")

                        if '/id/api/redirect' in url or 'epicgames.com' in url.lower():
                            code = await self._extract_epic_code_from_page(url)
                            if code:
                                if expected_store == 'epic':
                                    logger.info("[CDP] Found epic authorization code from page content")
                                    return code, 'epic'
                                else:
                                    logger.warning(f"[CDP] Ignoring epic code (expected: {expected_store})")

                        code, store = self._extract_code(url)
                        if code:
                            if store == expected_store:
                                logger.info(f"[CDP] Found {store} authorization code")
                                return code, store
                            else:
                                logger.warning(f"[CDP] Ignoring {store} code (expected: {expected_store})")

            except Exception as e:
                logger.debug(f"[CDP] Polling error (normal): {e}")

            await asyncio.sleep(poll_interval)

        logger.warning(f"[CDP] Poll timeout - no {expected_store} code found")
        return None, None

    # ── internal: Fetch interception (Microsoft only) ───────────────────────

    async def _monitor_ms_page_events(self, timeout: float) -> Tuple[Optional[str], Optional[str]]:
        """
        Intercept ALL network requests to oauth20_desktop.srf using the CDP
        Fetch domain connected to the browser-level target (/json/version).

        This catches the ?code= redirect at the network layer — before CEF
        processes or follows the redirect — regardless of which page or frame
        issues it.  The intercepted request is always continued so the browser
        behaves normally afterward.

        Why Fetch interception instead of Network events or page polling:
          - Network.requestWillBeSent fires AFTER the browser already decided to
            follow a redirect; for 302s the ?code= URL is gone before the next
            poll cycle.
          - Fetch.requestPaused fires BEFORE the request is processed, giving us
            a reliable synchronous hook into the redirect chain.
          - Browser-level target captures requests from ALL pages/popups.
        """
        import time
        try:
            import websockets
        except ImportError:
            logger.warning("[CDP-fetch] websockets not available; falling back to poll-only")
            return None, None

        # ── connect to the browser-level CDP target ───────────────────────────
        # /json/version returns the webSocketDebuggerUrl for the browser itself
        # (not a specific tab), which lets us intercept network requests globally.
        cef_port = self.cef_url.split(':')[2].split('/')[0]
        browser_ws_url = None
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{cef_port}/json/version", timeout=2
            ) as r:
                version_data = json.loads(r.read().decode())
            browser_ws_url = version_data.get('webSocketDebuggerUrl')
        except Exception as e:
            logger.debug(f"[CDP-fetch] /json/version failed: {e}")

        if not browser_ws_url:
            # Fallback: use first available page target
            try:
                with urllib.request.urlopen(self.cef_url, timeout=2) as r:
                    pages = json.loads(r.read().decode())
                browser_ws_url = pages[0].get('webSocketDebuggerUrl') if pages else None
            except Exception:
                pass

        if not browser_ws_url:
            logger.warning("[CDP-fetch] No WebSocket URL available for Fetch interception")
            return None, None

        logger.info(f"[CDP-fetch] Connecting for Fetch interception, timeout={timeout:.0f}s")

        try:
            async with websockets.connect(browser_ws_url, ping_interval=None, open_timeout=10) as ws:
                msg_id = 1

                async def send(method, params=None):
                    nonlocal msg_id
                    payload = json.dumps({'id': msg_id, 'method': method, 'params': params or {}})
                    await ws.send(payload)
                    msg_id += 1

                # Enable Fetch interception for oauth20_desktop.srf only
                await send('Fetch.enable', {
                    'patterns': [{'urlPattern': '*oauth20_desktop.srf*'}],
                })
                await asyncio.wait_for(ws.recv(), timeout=5)
                logger.info("[CDP-fetch] Fetch interception enabled for oauth20_desktop.srf")

                deadline = time.time() + timeout
                while time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get('method') != 'Fetch.requestPaused':
                        continue

                    params_     = msg.get('params', {})
                    request_id  = params_.get('requestId')
                    req_url     = params_.get('request', {}).get('url', '')

                    logger.debug(f"[CDP-fetch] Fetch.requestPaused: {req_url[:120]}")

                    # Always continue so the browser behaves normally
                    await send('Fetch.continueRequest', {'requestId': request_id})

                    if 'code=' in req_url:
                        code, store = self._extract_code(req_url)
                        if code and store == 'microsoft':
                            logger.info("[CDP-fetch] ✓ Microsoft code captured via Fetch interception")
                            try:
                                await send('Fetch.disable', {})
                            except Exception:
                                pass
                            return code, store

                    elif 'removed=true' in req_url:
                        # Seen removed=true without a prior ?code= in this session
                        logger.info("[CDP-fetch] removed=true without prior code — returning sentinel")
                        try:
                            await send('Fetch.disable', {})
                        except Exception:
                            pass
                        return '__removed__', 'microsoft'

        except Exception as e:
            logger.warning(f"[CDP-fetch] WebSocket error: {e}")

        logger.warning("[CDP-fetch] Fetch interception timed out")
        return None, None

    # ── public helpers ───────────────────────────────────────────────────────

    async def close_page_by_url(self, url_pattern: str) -> bool:
        """Close browser page matching URL pattern via CDP"""
        try:
            with urllib.request.urlopen(self.cef_url, timeout=2) as response:
                pages = json.loads(response.read().decode())

            for page in pages:
                if url_pattern in page.get('url', ''):
                    ws_url = page.get('webSocketDebuggerUrl')
                    if ws_url:
                        logger.info(f"[CDP] Closing page via CDP: {page.get('url', '')[:80]}...")
                        import websockets
                        async with websockets.connect(ws_url, ping_interval=None) as websocket:
                            await websocket.send(json.dumps({
                                'id': 1,
                                'method': 'Page.close',
                                'params': {}
                            }))
                            logger.info("[CDP] Page close command sent")
                            return True

            logger.warning(f"[CDP] No page found matching: {url_pattern}")
            return False

        except Exception as e:
            logger.error(f"[CDP] Error closing page: {e}")
            return False

    async def navigate_page_to_url(self, url_pattern: str, new_url: str) -> bool:
        """Navigate a browser page matching url_pattern to new_url via CDP."""
        try:
            with urllib.request.urlopen(self.cef_url, timeout=2) as response:
                pages = json.loads(response.read().decode())

            for page in pages:
                if url_pattern in page.get('url', ''):
                    ws_url = page.get('webSocketDebuggerUrl')
                    if ws_url:
                        logger.info(f"[CDP] Navigating page to new URL: {new_url[:80]}...")
                        import websockets
                        async with websockets.connect(ws_url, ping_interval=None) as websocket:
                            await websocket.send(json.dumps({
                                'id': 1,
                                'method': 'Page.navigate',
                                'params': {'url': new_url}
                            }))
                            await asyncio.wait_for(websocket.recv(), timeout=5)
                            logger.info("[CDP] Page navigation command sent")
                            return True

            logger.warning(f"[CDP] No page found matching: {url_pattern}")
            return False

        except Exception as e:
            logger.error(f"[CDP] Error navigating page: {e}")
            return False

    async def refresh_page_by_url(self, url_pattern: str) -> bool:
        """Refresh/reload a browser page matching URL pattern via CDP"""
        try:
            with urllib.request.urlopen(self.cef_url, timeout=2) as response:
                pages = json.loads(response.read().decode())

            for page in pages:
                if url_pattern in page.get('url', ''):
                    ws_url = page.get('webSocketDebuggerUrl')
                    if ws_url:
                        logger.info(f"[CDP] Refreshing page via CDP: {page.get('url', '')[:80]}...")
                        import websockets
                        async with websockets.connect(ws_url, ping_interval=None) as websocket:
                            await websocket.send(json.dumps({
                                'id': 1,
                                'method': 'Page.reload',
                                'params': {'ignoreCache': True}
                            }))
                            logger.info("[CDP] Page refresh command sent")
                            return True

            logger.warning(f"[CDP] No page found matching: {url_pattern}")
            return False

        except Exception as e:
            logger.error(f"[CDP] Error refreshing page: {e}")
            return False

    async def clear_cookies_for_domain(self, domain: str) -> bool:
        """Clear browser cookies for a specific domain via CDP.

        Uses Network.getCookies + Network.deleteCookies to remove only cookies
        that belong to the requested domain — does NOT wipe all browser cookies
        (which would log the user out of Epic, GOG, Amazon, etc.).
        """
        try:
            logger.info(f"[CDP] Clearing cookies for domain: {domain}")

            with urllib.request.urlopen(self.cef_url, timeout=2) as response:
                pages = json.loads(response.read().decode())

            if not pages:
                logger.error("[CDP] No pages available for CDP connection")
                return False

            ws_url = pages[0].get('webSocketDebuggerUrl')
            if not ws_url:
                logger.error("[CDP] No WebSocket URL available")
                return False

            import websockets
            async with websockets.connect(ws_url, ping_interval=None) as websocket:

                # Step 1: fetch all cookies
                await websocket.send(json.dumps({
                    'id': 1,
                    'method': 'Network.getAllCookies',
                    'params': {}
                }))
                raw = await asyncio.wait_for(websocket.recv(), timeout=5)
                data = json.loads(raw)
                cookies = data.get('result', {}).get('cookies', [])

                # Step 2: delete only cookies whose domain matches
                deleted = 0
                msg_id = 2
                for cookie in cookies:
                    cookie_domain = cookie.get('domain', '')
                    # cookie_domain may be ".live.com" or "login.live.com" etc.
                    if domain in cookie_domain or cookie_domain.lstrip('.') in domain:
                        await websocket.send(json.dumps({
                            'id': msg_id,
                            'method': 'Network.deleteCookies',
                            'params': {
                                'name':   cookie['name'],
                                'domain': cookie_domain,
                                'path':   cookie.get('path', '/'),
                            }
                        }))
                        await asyncio.wait_for(websocket.recv(), timeout=5)
                        msg_id += 1
                        deleted += 1

                logger.info(f"[CDP] Cleared {deleted} cookies for domain: {domain}")
                return True

        except Exception as e:
            logger.error(f"[CDP] Error clearing cookies: {e}")
            return False

    # ── private helpers ──────────────────────────────────────────────────────

    async def _extract_epic_code_from_page(self, url) -> Optional[str]:
        """Extract authorizationCode from browser page via CDP WebSocket"""
        try:
            logger.info(f"[CDP] Getting page details for: {url[:80]}...")

            with urllib.request.urlopen(self.cef_url, timeout=2) as response:
                pages = json.loads(response.read().decode())

            target_page = None
            for page in pages:
                if url in page.get('url', ''):
                    target_page = page
                    break

            if not target_page or 'webSocketDebuggerUrl' not in target_page:
                logger.error(f"[CDP] Could not find page or WebSocket URL for: {url[:80]}")
                return None

            ws_url = target_page['webSocketDebuggerUrl']
            import websockets
            async with websockets.connect(ws_url, ping_interval=None) as websocket:
                await websocket.send(json.dumps({
                    'id': 1,
                    'method': 'Runtime.evaluate',
                    'params': {
                        'expression': 'document.body.innerText',
                        'returnByValue': True
                    }
                }))

                response_text = await asyncio.wait_for(websocket.recv(), timeout=5)
                response_data = json.loads(response_text)

                if 'result' in response_data and 'result' in response_data['result']:
                    page_content = response_data['result']['result'].get('value', '')
                    logger.info(f"[CDP] Got page content from browser: {len(page_content)} chars")

                    match = re.search(r'"authorizationCode"\s*:\s*"([^"]+)"', page_content)
                    if match:
                        logger.info("[CDP] Extracted authorizationCode from browser page")
                        return match.group(1)

                    logger.info(f"[CDP] No authorizationCode in page content (first 200 chars): {page_content[:200]}")
                    return None
                else:
                    logger.error(f"[CDP] Unexpected response format: {response_data}")
                    return None

        except Exception as e:
            logger.error(f"[CDP] Error extracting Epic code via WebSocket: {e}")
            return None

    def _extract_code(self, url) -> Tuple[Optional[str], Optional[str]]:
        """Extract OAuth code from URL"""
        # Epic style (check first - more specific)
        if 'authorizationCode=' in url:
            match = re.search(r'authorizationCode=([^&\s]+)', url)
            if match:
                return match.group(1), 'epic'

        # Amazon style
        if 'amazon.com' in url.lower() and 'openid.oa2.authorization_code=' in url:
            match = re.search(r'openid\.oa2\.authorization_code=([^&\s]+)', url)
            if match:
                return match.group(1), 'amazon'

        # Microsoft / Xbox Live style
        # Match oauth20_desktop.srf regardless of whether login.live.com is
        # present in the URL (some CEF environments strip/rewrite the host).
        if 'oauth20_desktop.srf' in url and 'code=' in url:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if 'code' in params:
                logger.info("[CDP] Detected Microsoft OAuth redirect")
                return params['code'][0], 'microsoft'

        # GOG style
        if 'code=' in url:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if 'code' in params:
                return params['code'][0], 'gog'

        return None, None
