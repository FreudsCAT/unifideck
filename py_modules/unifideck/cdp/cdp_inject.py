from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from .cdp_client import CDPClient
logger = logging.getLogger(__name__)
STEAM_TAB_URL_MARKER = "steamloopback.host"
STYLE_ID_PREFIX = "unifideck-style-"
HIDE_PLAY_CSS = """
div[class*="appactionbutton_PlayButton"][data-app-id="__APP_ID__"],
div[class*="library_AppActionButton"][data-app-id="__APP_ID__"]
button[class*="play_PlayBtn"] {
 display: none !important;
}
""".strip()
def is_steam_ui_tab(page: dict[str, Any]) -> bool:
    """Check whether steam ui tab."""
    if not isinstance(page, dict):
        return False
    url = page.get("url", "")
    return STEAM_TAB_URL_MARKER in url
def escape_css_for_template_literal(css: str) -> str:
    """Escape CSS for template literal."""
    return (
    css.replace("\\", "\\\\")
    .replace("`", "\\`")
    .replace("${", "\\${")
    )
def build_marker_id(name: str) -> str:
    """Build marker ID."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return f"{STYLE_ID_PREFIX}{safe}"
class SteamCSSInjector:
    """Steam cssinjector."""
    def __init__(self, cdp_client: CDPClient) -> None:
        """Initialize the instance."""
        self._cdp = cdp_client
    async def connect_to_steam(self) -> bool:
        """Connect to steam."""
        try:
            return await self._cdp.connect(STEAM_TAB_URL_MARKER)
        except Exception as e:
            logger.warning("[cdp_inject] connect failed: %s", e)
            return False

    async def inject_css(self, css: str, marker: str) -> bool:

        """Inject CSS."""
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
            result = await self._cdp.eval_js(js)
            return bool(result)
        except Exception as e:
            logger.warning(
            "[cdp_inject] eval failed for %s: %s", marker, e,
            )
            return False
    async def remove_css(self, marker: str) -> bool:
        """Remove CSS."""
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
            logger.debug(
            "[cdp_inject] remove failed for %s: %s", marker, e,
            )
            return False
    async def hide_play_section(self, app_id: int) -> bool:
        """Hide play section."""
        css = HIDE_PLAY_CSS.replace("__APP_ID__", str(app_id))
        return await self.inject_css(css, f"app-{app_id}")
    async def show_play_section(self, app_id: int) -> bool:
        """Show play section."""
        return await self.remove_css(f"app-{app_id}")
_singleton_injector: SteamCSSInjector | None = None
async def get_cdp_client() -> SteamCSSInjector:
    """Get CDP client."""
    global _singleton_injector
    if _singleton_injector is None:
        from .cdp_client import CDPClient
        client = CDPClient()
        _singleton_injector = SteamCSSInjector(client)
    return _singleton_injector
async def shutdown_cdp_client() -> None:
    """Shutdown CDP client."""
    global _singleton_injector
    _singleton_injector = None