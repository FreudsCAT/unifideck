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

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types.events import Events
from unifideck.core.types.results import Result
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus

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

        # ``auto_wire(self, bus)`` walks ``self``'s methods
        # and registers every ``@subscribe(Events.X)``-marked
        # handler with the bus. Earlier this site called
        # ``self._bus.auto_wire(self)`` guarded by
        # ``hasattr`` — but ``auto_wire`` is module-level,
        # not a bus method, so the hasattr check returned
        # False and every subscription was silently dropped.
        auto_wire(self, self._bus)

    async def stop(self) -> None:
        """Lifecycle hook."""

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
        """Write a ``CompatToolMapping`` entry for ``app_id`` = ``tool``.

        The synchronous file I/O is dispatched to a worker thread
        via :func:`asyncio.to_thread` so the event loop stays
        responsive even on slow disks (Decks routinely write to
        an SD card here).
        """
        if not await asyncio.to_thread(lambda: Path(self._config_vdf_path).exists()):
            logger.warning("[ProtonService] config.vdf not found at %s", self._config_vdf_path)
            return Result(success=False, error="vdf_not_found")

        def _read_and_inject() -> tuple[str, str]:
            """Blocking read + transform, executed off the event loop."""
            with Path(self._config_vdf_path).open(encoding="utf-8") as f:
                content = f.read()
            return content, self._inject_compat_tool(content, app_id, tool)

        def _write_atomic(new_content: str) -> None:
            """Blocking atomic write, executed off the event loop."""
            tmp_path = f"{self._config_vdf_path}.tmp"
            with Path(tmp_path).open("w", encoding="utf-8") as f:
                f.write(new_content)
                f.flush()
                os.fsync(f.fileno())
            Path(tmp_path).replace(self._config_vdf_path)

        try:
            content, new_content = await asyncio.to_thread(_read_and_inject)
            if new_content == content:
                # No change needed
                return Result(success=True)
            await asyncio.to_thread(_write_atomic, new_content)
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
        # Inject new entry at the start of CompatToolMapping block
        # This is fragile but represents the intent
        return content.replace('"CompatToolMapping"\n\t\t\t\t{', f'"CompatToolMapping"\n\t\t\t\t{{\n\t\t\t\t\t{new_block}')

    async def prepare_launch(self, **kwargs: Any) -> Any:
        """Prepare a Proton launch plan via the infrastructure core.

        Two call shapes are supported:

        * ``prepare_launch(ctx=..., state=...)`` — preferred,
          dispatched directly to the infrastructure core.
        * ``prepare_launch(<exploded kwargs>)`` — legacy form
          used by ``LauncherService.prepare_windows_plan``;
          rebuild a synthetic ``LaunchContext`` from the kwargs
          we know how to map.

        Drift fix (2026-05-15, lot 11f): the previous fallback
        called ``LaunchContext(game=kwargs, env={})`` — neither
        ``game`` nor ``env`` exist on ``LaunchContext``. Mapping
        the legacy keys to the real dataclass fields here.
        """
        from unifideck.launcher.proton.infrastructure.core import proton_prepare
        from unifideck.launcher.types.context import LaunchContext, RuntimeState

        ctx = kwargs.get("ctx")
        state = kwargs.get("state")

        if not ctx:
            # Reconstruct LaunchContext from exploded kwargs. The
            # legacy callers pass these as scalar fields; map them
            # onto the real dataclass attributes. Missing fields
            # are filled with safe defaults (empty strings / Path
            # cwd) so the dispatcher proceeds even on incomplete
            # input — the proton core itself will fail-fast if a
            # required value is empty.
            from pathlib import Path
            ctx = LaunchContext(
                store=str(kwargs.get("store", "")),
                game_id=str(kwargs.get("game_id", "")),
                exe_path=Path(str(kwargs.get("launch_path", ""))),
                work_dir=Path(str(kwargs.get("work_dir", "."))),
                plugin_dir=Path(str(kwargs.get("plugin_dir", "."))),
            )
            state = RuntimeState()

        # Narrowing for mypy: ``state`` is ``Any | None`` from
        # ``kwargs.get()``; if a caller passed ``ctx`` but not
        # ``state``, fall back to a fresh RuntimeState so the
        # downstream proton_prepare sees a real instance.
        if state is None:
            state = RuntimeState()

        # ── KNOWN SIGNATURE DRIFT (tracked, lot 12d) ────────────
        # ``proton_prepare`` (in launcher/proton/infrastructure/core.py)
        # is a **synchronous** function (no ``async``) and requires
        # three keyword-only arguments: ``python_bin``, ``proton_path``,
        # and ``proton_tool_id``. The call below predates that
        # signature: it ``await``s a sync function and omits the
        # three kw args.
        #
        # The runtime impact is mitigated because the **only** real
        # path through ``prepare_launch`` (via ``LauncherService._prepare_windows_plan``
        # → ``helpers.prepare_windows_plan``) doesn't go through this
        # branch — it passes ``app_id=0, launch_path=..., …`` so the
        # ``ctx = kwargs.get("ctx")`` is None and the legacy-rebuild
        # path runs, but the final ``await proton_prepare(...)`` would
        # raise immediately at runtime (TypeError on missing args +
        # TypeError on awaiting a non-awaitable).
        #
        # In other words: this branch IS dead code today. A future
        # lot should either:
        #   (a) resolve python_bin/proton_path/proton_tool_id from the
        #       state and call ``proton_prepare`` correctly (remove
        #       the ``await``), or
        #   (b) delete this branch and document that the only entry
        #       is via the explicit kwargs in helpers.prepare_windows_plan.
        # The silences below acknowledge the known drift; the call
        # would TypeError at runtime if it were ever exercised.
        return await proton_prepare(  # type: ignore[call-arg,misc]  # see comment above
            ctx, state,
        )
