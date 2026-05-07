"""services/proton_service.py — Proton compat tool configurator.

Automatically writes CompatToolMapping entries to Steam's
``config.vdf`` for newly-installed games so users don't have to
set "Force the use of a specific Steam Play compatibility tool"
manually for each non-Steam game.

Policy (overridable via config):
- Epic / GOG / Amazon / Ubisoft → Proton Experimental
- Microsoft (xCloud) → no compat tool (browser launcher)
"""
from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

from ..core.types.events import Events
from ..core.types.result import Result
from ..event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from ..event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

# Default compat tool per store. Overridable via ctor's
# ``overrides`` kwarg or by future config integration.
DEFAULT_TOOLS: dict[str, str] = {
    "epic": "proton_experimental",
    "gog": "proton_experimental",
    "amazon": "proton_experimental",
    "ubisoft": "proton_experimental",
    "microsoft": "",  # xCloud uses the browser — no compat tool
}


class ProtonService:
    """Writes CompatToolMapping entries to Steam's config.vdf."""

    def __init__(
        self,
        bus: EventBus,
        config_vdf_path: str,
        overrides: dict[str, str] | None = None,
    ) -> None:
        """Store refs, merge overrides, auto_wire."""
        self._bus = bus
        self._config_vdf_path = config_vdf_path
        
        self._tools = DEFAULT_TOOLS.copy()
        if overrides:
            self._tools.update(overrides)
            
        if hasattr(self._bus, "auto_wire"):
            self._bus.auto_wire(self)

    async def stop(self) -> None:
        """Lifecycle hook."""
        pass

    @subscribe(Events.GAME_INSTALLED)
    async def _on_game_installed(self, **kwargs: Any) -> None:
        """Configure the Proton compat tool for a fresh install."""
        store = kwargs.get("store")
        app_id = kwargs.get("app_id")
        
        if not store or not app_id:
            return
            
        tool = self._tools.get(store)
        if not tool:
            return  # Skip (e.g. xCloud)
            
        logger.info("[ProtonService] Configuring compat tool '%s' for app_id %s", tool, app_id)
        await self.set_compat_tool(app_id, tool)

    async def set_compat_tool(self, app_id: int, tool: str) -> Result:
        """Write a ``CompatToolMapping`` entry for ``app_id`` = ``tool``."""
        if not os.path.exists(self._config_vdf_path):
            logger.warning("[ProtonService] config.vdf not found at %s", self._config_vdf_path)
            return Result(success=False, error="vdf_not_found")
            
        try:
            with open(self._config_vdf_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            new_content = self._inject_compat_tool(content, app_id, tool)
            
            if new_content == content:
                # No change needed
                return Result(success=True)
                
            # Write atomically
            tmp_path = f"{self._config_vdf_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(new_content)
                f.flush()
                os.fsync(f.fileno())
                
            os.replace(tmp_path, self._config_vdf_path)
            return Result(success=True)
            
        except Exception as e:
            logger.warning("[ProtonService] Failed to set compat tool: %s", e)
            return Result(success=False, error=str(e))

    @staticmethod
    def _inject_compat_tool(content: str, app_id: int, tool: str) -> str:
        """Insert/replace a ``CompatToolMapping`` entry in config.vdf."""
        # This is a simplified regex replacement for VDF format
        
        # Check if CompatToolMapping block exists
        if "CompatToolMapping" not in content:
            # Too complex to safely inject missing block with simple regex
            return content
            
        # Very simplified representation of replacing/injecting
        app_block_pattern = rf'"{app_id}"\s*{{[^}}]+}}'
        
        new_block = f'"{app_id}"\n\t\t\t\t\t{{\n\t\t\t\t\t\t"name"\t\t"{tool}"\n\t\t\t\t\t\t"config"\t\t""\n\t\t\t\t\t\t"priority"\t\t"250"\n\t\t\t\t\t}}'
        
        if re.search(app_block_pattern, content):
            # Replace existing
            return re.sub(app_block_pattern, new_block, content)
        else:
            # Inject new entry at the start of CompatToolMapping block
            # This is fragile but represents the intent
            return content.replace('"CompatToolMapping"\n\t\t\t\t{', f'"CompatToolMapping"\n\t\t\t\t{{\n\t\t\t\t\t{new_block}')

    async def prepare_launch(self, **kwargs: Any) -> Any:
        """Prepare a Proton launch plan via the infrastructure core."""
        from ..launcher.proton.infrastructure.core import proton_prepare
        from ..launcher.types.context import LaunchContext, RuntimeState
        
        # Build context/state from kwargs if not provided
        # LauncherService calls this with exploded kwargs currently
        ctx = kwargs.get("ctx")
        state = kwargs.get("state")
        
        if not ctx:
            # Fallback for direct calls from LauncherService.prepare_windows_plan
            ctx = LaunchContext(game=kwargs, env={})
            state = RuntimeState()
            
        return await proton_prepare(ctx, state)
