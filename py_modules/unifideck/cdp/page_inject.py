"""
Generic CDP page-script injection helpers.

These helpers connect to a page target on a Chromium/Chrome DevTools port
and inject one or more JavaScript snippets both for future navigations and
the current document. They use ``aiohttp`` so they work in environments
where the optional ``websockets`` dependency is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Iterable, Optional, Sequence

import aiohttp

logger = logging.getLogger(__name__)


def _matches_url(url: str, url_patterns: Sequence[str]) -> bool:
    """Return True when the target URL matches any configured substring."""
    if not url_patterns:
        return True
    return any(pattern in url for pattern in url_patterns)


async def list_page_targets(
    cdp_port: int,
    timeout: float = 3.0,
) -> Sequence[Dict[str, Any]]:
    """List page-like CDP targets for a browser port."""
    url = f"http://127.0.0.1:{cdp_port}/json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CDP /json returned {resp.status}")
            payload = await resp.json(content_type=None)

    if not isinstance(payload, list):
        return []

    return [
        target for target in payload
        if target.get("type", "page") in ("page", "webview")
        and target.get("webSocketDebuggerUrl")
    ]


async def wait_for_matching_page(
    cdp_port: int,
    url_patterns: Sequence[str],
    timeout: float = 30.0,
    poll_delay: float = 0.3,
) -> Optional[Dict[str, Any]]:
    """Poll the DevTools target list until a matching page becomes available."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            targets = await list_page_targets(cdp_port)
            for target in targets:
                if _matches_url(target.get("url", ""), url_patterns):
                    return target
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(f"[CDP-page-inject] waiting for page failed: {exc}")

        await asyncio.sleep(poll_delay)

    return None


async def _send_command(
    websocket: aiohttp.ClientWebSocketResponse,
    msg_id: int,
    method: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """Send a CDP command and wait for its matching response."""
    await websocket.send_json({
        "id": msg_id,
        "method": method,
        "params": params or {},
    })

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for {method} response")

        message = await websocket.receive(timeout=remaining)
        if message.type != aiohttp.WSMsgType.TEXT:
            if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                raise RuntimeError("CDP websocket closed")
            if message.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError("CDP websocket error")
            continue

        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError:
            continue

        if payload.get("id") != msg_id:
            continue

        if "error" in payload:
            raise RuntimeError(f"{method} failed: {payload['error']}")

        return payload


async def inject_scripts(
    cdp_port: int,
    sources: Iterable[str],
    *,
    url_patterns: Sequence[str],
    timeout: float = 30.0,
    logger_prefix: str = "CDP-page-inject",
) -> bool:
    """Inject scripts into a matching CDP page target.

    Each source is registered for future navigations via
    ``Page.addScriptToEvaluateOnNewDocument`` and then executed immediately
    in the current document via ``Runtime.evaluate``.
    """
    sources = [source for source in sources if source]
    if not sources:
        logger.warning(f"[{logger_prefix}] No sources provided for injection")
        return False

    target = await wait_for_matching_page(
        cdp_port,
        url_patterns=url_patterns,
        timeout=timeout,
    )
    if not target:
        logger.warning(
            f"[{logger_prefix}] No matching page found on port {cdp_port} "
            f"for patterns: {', '.join(url_patterns) or '<any>'}"
        )
        return False

    ws_url = target.get("webSocketDebuggerUrl")
    target_url = target.get("url", "")
    logger.info(f"[{logger_prefix}] Injecting into {target_url[:120]}")

    session = aiohttp.ClientSession()
    try:
        async with session.ws_connect(ws_url, heartbeat=10, autoping=True) as websocket:
            msg_id = 9000
            for source in sources:
                await _send_command(
                    websocket,
                    msg_id,
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": source},
                )
                msg_id += 1

            for source in sources:
                await _send_command(
                    websocket,
                    msg_id,
                    "Runtime.evaluate",
                    {
                        "expression": source,
                        "userGesture": True,
                        "awaitPromise": False,
                    },
                )
                msg_id += 1
    finally:
        await session.close()

    logger.info(f"[{logger_prefix}] Script injection complete")
    return True


async def evaluate_script(
    cdp_port: int,
    expression: str,
    *,
    url_patterns: Sequence[str],
    timeout: float = 30.0,
    logger_prefix: str = "CDP-page-eval",
) -> Any:
    """Evaluate JavaScript in a matching page and return its JSON value."""
    target = await wait_for_matching_page(
        cdp_port,
        url_patterns=url_patterns,
        timeout=timeout,
    )
    if not target:
        raise RuntimeError(
            f"[{logger_prefix}] No matching page found for: "
            f"{', '.join(url_patterns) or '<any>'}"
        )

    session = aiohttp.ClientSession()
    try:
        async with session.ws_connect(
            target["webSocketDebuggerUrl"],
            heartbeat=10,
            autoping=True,
        ) as websocket:
            result = await _send_command(
                websocket,
                9800,
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "userGesture": True,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
    finally:
        await session.close()

    return result.get("result", {}).get("result", {}).get("value")
