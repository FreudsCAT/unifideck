"""Proton compat-tool assignment — sets Steam's per-app compatibility tool.

OP-12b | py_modules/unifideck/services/proton_service.py

``ProtonService`` is the Steam-side bridge that ensures freshly-
registered shortcuts run under a Proton version. When a game is
installed on a non-Steam store (Epic, GOG, Amazon, Ubisoft) and
Unifideck creates a Steam shortcut for it, Steam by default would
try to run the Windows executable natively (which fails). Setting
the per-app ``CompatToolMapping`` in ``config.vdf`` tells Steam to
launch the shortcut through Proton instead.

The service subscribes to ``GAME_INSTALLED`` and reads the
per-store default tool from a hard-coded table (``DEFAULT_TOOLS``) —
all stores default to ``proton_experimental`` except Microsoft
(xCloud) which doesn't need a compat tool because it streams in a
browser. Overrides can be passed to the constructor for testing or
non-default setups.

VDF editing is regex-based rather than using a full VDF parser
because ``config.vdf`` is Steam-owned and we don't want to risk
re-serialising fields whose schema we don't fully understand —
surgical edits are safer.
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
    """Set Steam compat-tool mappings when a game is installed."""

    def __init__(
        self,
        bus: EventBus,
        config_vdf_path: str,
        overrides: dict[str, str] | None = None,
    ) -> None:
        """Wire the service to the bus and prepare its tool table.

        Args:
            bus: live event bus on which the service subscribes to
                ``GAME_INSTALLED``.
            config_vdf_path: absolute path to Steam's ``config.vdf``
                (the file holding ``CompatToolMapping``).
            overrides: optional per-store override of the default
                tool table. Useful for tests or for users who
                pin a specific Proton version.
        """
        self._bus = bus
        self._config_vdf = config_vdf_path
        self._tools: dict[str, str] = {**DEFAULT_TOOLS, **(overrides or {})}
        from ..event_bus.event_bus_devex import auto_wire

        auto_wire(self, self._bus)
        logger.info("[ProtonService] wired (1 subscription)")

    async def stop(self) -> None:
        """Unsubscribe the ``GAME_INSTALLED`` handler on shutdown.

        Symmetric to ``__init__``'s auto-wire. Removes the
        subscription so the bus no longer holds a reference to
        this instance after the plugin unloads.
        """
        self._bus.off(Events.GAME_INSTALLED, self._on_game_installed)

    @subscribe(Events.GAME_INSTALLED)
    async def _on_game_installed(self, **kwargs) -> None:
        """Apply the per-store default Proton tool to a freshly-added app.

        Reads ``app_id`` and ``store`` from the event payload, looks
        up the default tool for the store, and delegates to
        ``set_compat_tool``. Skips silently if either ``app_id`` is
        missing or the store has no default tool (e.g. Microsoft).
        """
        app_id = kwargs.get("app_id")
        store = kwargs.get("store", "")
        if not app_id:
            return
        tool = self._tools.get(store, "")
        if not tool:
            return
        await self.set_compat_tool(app_id, tool)

    async def set_compat_tool(self, app_id: int, tool: str) -> Result:
        """Set the compat-tool mapping for ``app_id`` to ``tool``.

        Reads ``config.vdf``, patches the ``CompatToolMapping``
        section for the given app id (creating the section if
        absent), and writes the result back. The write is skipped
        when the patched content is identical to the original
        (idempotency — calling this twice with the same tool is a
        no-op on the second call).

        Args:
            app_id: Steam app id of the shortcut to configure.
            tool: name of the Proton tool (e.g.
                ``"proton_experimental"``, ``"proton_ge_8_7"``).

        Returns:
            ``Result(success=True)`` on success or no-op,
            ``Result(success=False, error=…)`` on failure
            (``config_vdf_missing``, ``config_vdf_read_failed``, or
            the underlying I/O exception message).
        """
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
        """Surgically rewrite ``content`` to set ``app_id`` → ``tool``.

        Three cases:

        1. **App entry exists** — find the block matching
           ``"<app_id>" { "name" "<old>" … }`` and replace it
           wholesale with the new mapping.
        2. **App entry absent, section exists** — find
           ``"CompatToolMapping" {`` and insert the new mapping
           right after the opening brace.
        3. **Section absent** — append a complete
           ``CompatToolMapping`` section at the end of the file.

        Whitespace and quoting mimic Steam's own layout so the file
        remains readable in case the user opens it manually.

        Args:
            content: full text of the existing ``config.vdf``.
            app_id: Steam app id to set the mapping for.
            tool: Proton tool name.

        Returns:
            The patched VDF text. Identical to the input if and
            only if the file already had the requested mapping.
        """
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
