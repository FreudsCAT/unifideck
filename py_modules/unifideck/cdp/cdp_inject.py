"""Steam UI CSS / DOM mutation via the Chrome DevTools Protocol.

``SteamCSSInjector.inject_css`` / ``remove_css`` provide generic
``<style>`` tag management for arbitrary CSS rules, used by layout /
theming helpers.

(Steam's native PlaySection is no longer hidden here — that moved to
a synchronous, declarative CSS rule rendered frontend-side; see
``nativePlayHideCss`` and ``AppDetailsPatch``.)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cdp_client import CDPClient

logger = logging.getLogger(__name__)

STEAM_TAB_URL_MARKER = "steamloopback.host"
STYLE_ID_PREFIX = "unifideck-style-"


def is_steam_ui_tab(page: dict[str, Any]) -> bool:
    """Check whether the CDP page handle is Steam's UI tab."""
    if not isinstance(page, dict):
        return False  # type: ignore[unreachable]
    url = page.get("url", "")
    return STEAM_TAB_URL_MARKER in url


def escape_css_for_template_literal(css: str) -> str:
    """Escape a CSS string for use inside a JS template literal."""
    return (
        css.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("${", "\\${")
    )


def build_marker_id(name: str) -> str:
    """Build a DOM id for an injected ``<style>`` tag."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return f"{STYLE_ID_PREFIX}{safe}"


class SteamCSSInjector:
    """CDP-mediated DOM / CSS mutation for the Steam UI tab."""

    def __init__(self, cdp_client: CDPClient) -> None:
        """Initialize the instance."""
        self._cdp = cdp_client

    async def connect_to_steam(self) -> bool:
        """Connect to the Steam UI page over CDP."""
        try:
            return await self._cdp.connect(STEAM_TAB_URL_MARKER)
        except Exception as e:
            logger.warning("[cdp_inject] connect failed: %s", e)
            return False

    async def inject_css(self, css: str, marker: str) -> bool:
        """Inject (or update in place) a ``<style>`` tag keyed by ``marker``."""
        marker_id = build_marker_id(marker)
        escaped = escape_css_for_template_literal(css)
        js = f"""
        (() => {{
            const id = "{marker_id}";
            let el = document.getElementById(id);
            if (!el) {{
                el = document.createElement("style");
                el.id = id;
                document.head.appendChild(el);
            }}
            el.textContent = `{escaped}`;
            return true;
        }})()
        """
        try:
            return bool(await self._cdp.eval_js(js))
        except Exception as e:
            logger.warning("[cdp_inject] eval failed for %s: %s", marker, e)
            return False

    async def remove_css(self, marker: str) -> bool:
        """Remove a previously-injected ``<style>`` tag."""
        marker_id = build_marker_id(marker)
        js = f"""
        (() => {{
            const el = document.getElementById("{marker_id}");
            if (el) {{ el.remove(); return true; }}
            return false;
        }})()
        """
        try:
            return bool(await self._cdp.eval_js(js))
        except Exception as e:
            logger.debug("[cdp_inject] remove failed for %s: %s", marker, e)
            return False


_singleton_injector: SteamCSSInjector | None = None
_CDP_CONNECT_TIMEOUT_S = 5.0


async def _build_connected_injector() -> SteamCSSInjector | None:
    """Construct a fresh ``SteamCSSInjector`` and connect it.

    Returns ``None`` if the connect fails so the caller can avoid
    caching a dead singleton.
    """
    from .cdp_client import CDPClient
    injector = SteamCSSInjector(CDPClient())
    try:
        ok = await asyncio.wait_for(
            injector.connect_to_steam(),
            timeout=_CDP_CONNECT_TIMEOUT_S,
        )
    except (TimeoutError, Exception) as e:
        logger.warning("[cdp_inject] connect to Steam UI tab failed: %s", e)
        return None
    if not ok:
        logger.warning(
            "[cdp_inject] connect to Steam UI tab returned False "
            "(no '%s' target found?)",
            STEAM_TAB_URL_MARKER,
        )
        return None
    logger.info(
        "[cdp_inject] connected to Steam UI tab (marker=%r)",
        STEAM_TAB_URL_MARKER,
    )
    return injector


async def get_cdp_client() -> SteamCSSInjector | None:
    """Return the process-wide ``SteamCSSInjector`` singleton.

    Constructs and connects on first call. If the cached singleton's
    WebSocket has dropped (Steam restart, CEF reload), reconnects from
    scratch. Returns ``None`` if connect fails so RPC handlers can
    surface a clean error instead of caching a dead client.
    """
    global _singleton_injector
    if _singleton_injector is None:
        _singleton_injector = await _build_connected_injector()
        return _singleton_injector
    if not _singleton_injector._cdp.connected:
        logger.debug("[cdp_inject] singleton present but disconnected; reconnecting")
        await _singleton_injector._cdp.disconnect()
        _singleton_injector = await _build_connected_injector()
    return _singleton_injector


async def shutdown_cdp_client() -> None:
    """Drop the singleton (called on plugin unload)."""
    global _singleton_injector
    if _singleton_injector is not None:
        try:
            await _singleton_injector._cdp.disconnect()
        except Exception as e:
            logger.debug("[cdp_inject] disconnect on shutdown skipped: %s", e)
    _singleton_injector = None
