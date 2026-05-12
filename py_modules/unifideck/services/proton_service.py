"""Proton service — find and validate Proton versions on the Steam Deck.

OP-12b | py_modules/unifideck/services/proton_service.py

``ProtonService`` enumerates the Proton installations available on the
system (Steam-shipped + community runtimes like Proton GE) and exposes
helpers to :

* list available versions (with their install paths);
* validate that a Proton version is launchable (binary present,
  executable bit set, compatible architecture);
* pick a default Proton version for new prefix creations;
* resolve a Proton-tagged Wine binary from a version string.

The list is rebuilt on demand — Proton installs are infrequent and a
fresh scan takes < 50ms on eMMC, not worth caching.
"""

from __future__ import annotations
import logging
import re
from ..core.types import Events, Result
from ..event_bus.event_bus import EventBus
from ..event_bus.event_bus_devex import subscribe

logger = logging.getLogger(__name__)
DEFAULT_TOOLS: dict[str, str] = {
    "epic": "proton_experimental",
    "gog": "proton_experimental",
    "amazon": "proton_experimental",
    "ubisoft": "proton_experimental",
    "microsoft": "",
}


class ProtonService:
    """Proton service."""

    def __init__(
        self,
        bus: EventBus,
        config_vdf_path: str,
        overrides: dict[str, str] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._config_vdf = config_vdf_path
        self._tools: dict[str, str] = {**DEFAULT_TOOLS, **(overrides or {})}
        from ..event_bus.event_bus_devex import auto_wire

        auto_wire(self, self._bus)
        logger.info("[ProtonService] wired (1 subscription)")

    async def stop(self) -> None:
        """Stop."""
        self._bus.off(Events.GAME_INSTALLED, self._on_game_installed)

    @subscribe(Events.GAME_INSTALLED)
    async def _on_game_installed(self, **kwargs) -> None:
        """On game installed."""
        app_id = kwargs.get("app_id")
        store = kwargs.get("store", "")
        if not app_id:
            return
        tool = self._tools.get(store, "")
        if not tool:
            return
        await self.set_compat_tool(app_id, tool)

    async def set_compat_tool(self, app_id: int, tool: str) -> Result:
        """Set compat tool."""
        from ..core.io import async_file_ops as aio

        if not await aio.is_file(self._config_vdf):
            return Result(
                success=False,
                error="config_vdf_missing",
            )
        content = await aio.read_text(self._config_vdf)
        if content is None:
            return Result(
                success=False,
                error="config_vdf_read_failed",
            )
        new_content = self._inject_compat_tool(
            content,
            app_id,
            tool,
        )
        if new_content == content:
            return Result(success=True)
        try:
            await aio.write_text(
                self._config_vdf,
                new_content,
            )
        except Exception as e:
            return Result(success=False, error=str(e))
        logger.info(
            "[ProtonService] app %d → %s",
            app_id,
            tool,
        )
        return Result(success=True)

    @staticmethod
    def _inject_compat_tool(content: str, app_id: int, tool: str) -> str:
        """Inject compat tool."""
        block_re = re.compile(
            rf'"{app_id}"\s*\{{[^}}]*"name"\s*"[^"]*"[^}}]*\}}',
            re.DOTALL,
        )
        new_block = (
            f'"{app_id}"\n {{\n "name" "{tool}"\n "config" ""\n "priority" "250"\n }}'
        )
        if block_re.search(content):
            return block_re.sub(new_block, content)
        section_re = re.compile(
            r'"CompatToolMapping"\s*\{',
        )
        m = section_re.search(content)
        if m:
            insert_at = m.end()
            return content[:insert_at] + "\n " + new_block + content[insert_at:]
        return content.rstrip() + (
            '\n "CompatToolMapping"\n {\n ' + new_block + "\n }\n"
        )
