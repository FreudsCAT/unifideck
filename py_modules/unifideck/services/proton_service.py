"""services/proton_service.py — Proton provisioning for non-Steam games.

Two responsibilities:

1. On plugin load (``start``), background-install the *latest*
   GE-Proton released online (``ge_installer.ensure_latest_ge``) so
   games default to the newest GE-Proton without blocking any launch.
   Best-effort: offline/failure leaves the launcher to fall back to
   Proton Experimental at launch time.

2. On ``GAME_INSTALLED``, optionally force a per-store compat tool in
   Steam's ``config.vdf`` (``set_compat_tool``). This is now a no-op by
   default — see ``DEFAULT_TOOLS``: the launcher selects Proton itself
   and forcing a tool here would pin every game to it (via
   ``proton_settings.json``), defeating the "latest GE-Proton by
   default" policy. A forced tool can still be reinstated per store or
   via the ctor ``overrides`` kwarg.
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

# Per-store compat tool to FORCE on install. Empty everywhere by
# design: the launcher picks Proton itself (latest GE-Proton by
# default, Proton Experimental as the offline fallback) and
# ``useLaunchPrep`` clears Force-Compat before ``RunGame``. Forcing
# ``proton_experimental`` here used to pin every game to Experimental
# via ``proton_settings.json``, defeating the latest-GE default — so we
# set nothing. Overridable via the ctor ``overrides`` kwarg.
DEFAULT_TOOLS: dict[str, str] = {
    "epic": "",
    "gog": "",
    "amazon": "",
    "ubisoft": "",
    "battlenet": "",
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
        self._ge_task: asyncio.Task[None] | None = None

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

    def set_config_vdf_path(self, config_vdf_path: str) -> None:
        """Re-point at a different user's ``localconfig.vdf`` at runtime.

        Driven by :func:`unifideck.steam.current_user.rebind_user_paths` when
        the active Steam user is (re)confirmed after boot, so per-game Proton
        compat entries are written to the account the user is logged into.
        """
        self._config_vdf_path = config_vdf_path

    async def start(self) -> None:
        """Background-install the latest GE-Proton on plugin load.

        Non-blocking: the (potentially large) GitHub fetch + extract
        runs off the event loop via ``ge_installer.ensure_latest_ge``
        so booting and game launches are never gated on it. Failures
        (offline / GitHub down) are swallowed — the launcher falls
        back to Proton Experimental at launch time. The task reference
        is retained so it isn't garbage-collected mid-flight.
        """
        self._ge_task = asyncio.create_task(self._ensure_latest_ge())

    async def _ensure_latest_ge(self) -> None:
        """Background-install the latest GE-Proton, toasting a new install.

        Stays silent when the latest is already present (the common
        case): the install/ready toasts fire only when a download
        actually happens, so a normal boot is quiet.
        """
        try:
            from unifideck.launcher.proton.infrastructure import ge_installer

            tag = await asyncio.to_thread(ge_installer.get_latest_ge_tag)
            if not tag:
                logger.info(
                    "[ProtonService] latest GE-Proton unavailable "
                    "(offline?); launcher will use Proton Experimental",
                )
                return
            if await asyncio.to_thread(ge_installer.is_valid_ge_install, tag):
                logger.info("[ProtonService] latest GE-Proton already installed: %s", tag)
                return
            # A download is needed → tell the user it's happening.
            await self._emit_proton_toast(
                "toasts.launcher.installingProton",
                "toasts.launcher.attemptingInstall",
                tag,
            )
            result = await asyncio.to_thread(ge_installer.ensure_latest_ge)
        except Exception:
            logger.exception("[ProtonService] background GE-Proton install failed")
            return
        if result:
            _path, installed_tag = result
            logger.info("[ProtonService] latest GE-Proton ready: %s", installed_tag)
            await self._emit_proton_toast(
                "toasts.launcher.protonReadyTitle",
                "toasts.launcher.protonReadyBody",
                installed_tag,
            )
        else:
            logger.warning(
                "[ProtonService] GE-Proton install failed; "
                "launcher will fall back to Proton Experimental",
            )

    async def _emit_proton_toast(
        self, title_key: str, body_key: str, version: str,
    ) -> None:
        """Best-effort LAUNCHER_STAGE toast for GE-Proton install progress."""
        try:
            from unifideck.launcher.rpc import emit_stage

            await emit_stage(
                self._bus,
                i18n_title_key=title_key,
                i18n_key=body_key,
                game_title="",
                i18n_params={"version": version},
                priority="normal",
            )
        except Exception:
            logger.warning("[ProtonService] proton toast emit failed", exc_info=True)

    async def stop(self) -> None:
        """Cancel the background GE-Proton install if still running."""
        if self._ge_task is not None and not self._ge_task.done():
            self._ge_task.cancel()

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
