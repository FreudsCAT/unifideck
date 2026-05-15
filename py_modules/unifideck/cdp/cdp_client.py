from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any, cast

from unifideck.utils.config_helpers import get_cfg

logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    from unifideck.config import ConfigManager
class CDPClient:
    """Cdpclient."""
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        """Initialize the instance."""
        self._host = host or get_cfg(config, "cdp.host", "127.0.0.1")
        self._port = port or int(get_cfg(config, "cdp.port", 8080))
        self._eval_timeout = float(
            get_cfg(config, "cdp.eval_timeout_seconds", 30)
        )
        self._response_timeout = float(
            get_cfg(config, "cdp.response_timeout_seconds", 10)
        )
        self._ws = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._recv_task: asyncio.Task | None = None
    async def connect(self, target_url_substring: str = "") -> bool:
        """Connect."""
        targets = await self._list_targets()
        target = self._pick_target(targets, target_url_substring)
        if not target or "webSocketDebuggerUrl" not in target:
            return False
        try:
            import websockets
            self._ws = await websockets.connect(
                target["webSocketDebuggerUrl"],
                max_size=None,
            )
        except Exception:
            logger.exception("[CDPClient] ws connect failed")
            return False
        self._recv_task = asyncio.create_task(self._recv_loop())
        return True
    async def disconnect(self) -> None:
        """Disconnect."""
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def inject_css(self, css: str) -> bool:

        """Inject CSS."""
        expression = (
            "(() => {"
            "const s = document.createElement('style');"
            f"s.textContent = {json.dumps(css)};"
            "document.head.appendChild(s);"
            "return true; })()"
        )
        result = await self.evaluate(expression)
        return bool(result and result.get("result", {}).get("value"))
    async def evaluate(self, expression: str) -> dict | None:
        """Evaluate."""
        return await self._send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
    async def eval_js(self, expression: str) -> Any:
        """Eval js."""
        result = await self.evaluate(expression)
        if not result:
            return None
        return result.get("result", {}).get("value")
    async def list_targets(self) -> list[dict[str, Any]]:
        """List targets."""
        return await self._list_targets()
    async def close_target(self, target_id: str) -> bool:
        """Close target."""
        if not target_id:
            return False
        import aiohttp
        url = (
            f"http://{self._host}:{self._port}"
            f"/json/close/{target_id}"
        )
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(url, timeout=5) as resp,
            ):
                return cast("bool", resp.status == 200)
        except Exception as e:
            logger.debug(
                "[CDPClient] close_target failed: %s", e,
            )
            return False
    async def navigate(self, url: str) -> bool:
        """Navigate."""
        result = await self._send("Page.navigate", {"url": url})
        return result is not None
    async def wait_for_url(self, substring: str,
                           timeout: float | None = None) -> str | None:
        """Wait for URL."""
        deadline = asyncio.get_event_loop().time() + (
            timeout
            if timeout is not None
            else self._eval_timeout
        )
        while asyncio.get_event_loop().time() < deadline:
            result = await self.evaluate(
                "window.location.href",
            )
            if result:
                url = (
                    result.get("result", {}).get("value")
                    or ""
                )
                if substring in url:
                    return url
            await asyncio.sleep(0.5)
        return None

    async def _list_targets(self) -> list[dict[str, Any]]:

        """List targets."""
        import aiohttp
        url = f"http://{self._host}:{self._port}/json"
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(url, timeout=5) as resp,
            ):
                if resp.status != 200:
                    return []
                return cast("list[dict[str, Any]]", await resp.json())
        except Exception as e:
            logger.debug(
                "[CDPClient] list targets failed: %s", e,
            )
            return []
    @staticmethod
    def _pick_target(targets: list[dict[str, Any]],
                     substring: str) -> dict[str, Any] | None:
        """Pick target."""
        for t in targets:
            if t.get("type") != "page":
                continue
            if not substring or substring in t.get("url", ""):
                return t
        return None
    async def _send(self, method: str,
                    params: dict[str, Any]) -> dict | None:
        """Send."""
        if not self._ws:
            return None
        self._request_id += 1
        req_id = self._request_id
        message = {
            "id": req_id,
            "method": method,
            "params": params,
        }
        future: asyncio.Future = (
            asyncio.get_event_loop().create_future()
        )
        self._pending[req_id] = future
        try:
            await self._ws.send(json.dumps(message))
            return await asyncio.wait_for(
                future, timeout=self._response_timeout,
            )
        except TimeoutError:
            logger.warning(
                "[CDPClient] timeout on %s", method,
            )
            return None
        finally:
            self._pending.pop(req_id, None)
    async def _recv_loop(self) -> None:
        """Recv loop."""
        if self._ws is None:
            logger.warning(
                "[CDPClient] _recv_loop started before connect()",
            )
            return
        ws = self._ws
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                req_id = msg.get("id")
                if req_id and req_id in self._pending:
                    self._pending[req_id].set_result(
                        msg.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[CDPClient] recv loop error: %s", e)
