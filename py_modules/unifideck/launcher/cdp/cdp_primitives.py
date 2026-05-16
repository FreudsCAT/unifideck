from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, cast

import aiohttp

from unifideck.cdp.page_inject import list_page_targets

logger = logging.getLogger(__name__)
async def wait_for_titled_target(
    cdp_port: int,
    title_substring: str,
    *,
    timeout: float = 15.0,  # noqa: ASYNC109 — timeout is API value passed to underlying lib (urllib/aiohttp/subprocess), not an asyncio.timeout() wrapper
    poll_delay: float = 0.25,
) -> dict[str, Any] | None:
    """Wait for titled target."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            targets = await list_page_targets(cdp_port, timeout=3.0)
            for target in targets:
                if title_substring in str(target.get("title", "")):
                    return dict(target)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[cdp] waiting for target failed: %s", exc)
        await asyncio.sleep(poll_delay)
    return None
async def close_target(cdp_port: int, target_id: str) -> None:
    """Close target."""
    close_url = f"http://127.0.0.1:{cdp_port}/json/close/{target_id}"
    async with aiohttp.ClientSession() as session:
        with contextlib.suppress(Exception):
            async with session.get(
                close_url,
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as response:
                await response.read()
async def close_titled_targets(
    cdp_port: int, title_substring: str,
) -> None:
    """Close titled targets."""
    with contextlib.suppress(Exception):
        targets = await list_page_targets(cdp_port, timeout=3.0)
        for target in targets:
            if title_substring in str(target.get("title", "")):
                await close_target(cdp_port, str(target["id"]))

async def cdp_command(
    websocket: aiohttp.ClientWebSocketResponse,
    msg_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:

    """Cdp command."""
    await websocket.send_json(
        {
            "id": msg_id,
            "method": method,
            "params": params or {},
        },
    )
    while True:
        message = await websocket.receive(timeout=15)
        if message.type != aiohttp.WSMsgType.TEXT:
            if message.type in (
                aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
            ):
                raise RuntimeError("CDP websocket closed")
            if message.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError("CDP websocket error")
            continue
        payload = json.loads(message.data)
        if payload.get("id") != msg_id:
            continue
        if "error" in payload:
            raise RuntimeError(f"{method} failed: {payload['error']}")
        return cast("dict[str, Any]", payload)
async def evaluate_in_target(
    target: dict[str, Any],
    expression: str,
    *,
    return_by_value: bool = True,
) -> dict[str, Any]:
    """Evaluate in target."""
    async with aiohttp.ClientSession() as session, session.ws_connect(
        target["webSocketDebuggerUrl"],
        heartbeat=10,
        autoping=True,
    ) as websocket:
        return await cdp_command(
            websocket,
            9001,
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": return_by_value,
                "userGesture": True,
            },
        )
