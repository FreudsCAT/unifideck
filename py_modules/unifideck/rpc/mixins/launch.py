"""LaunchRPCMixin — launch lifecycle + circuit breaker + diagnostics RPC.

OP-26j | py_modules/unifideck/rpc/mixins/launch.py

Mixin merging two slices that the handler groups split apart:

* **Game lifecycle pass-through** (``notify_game_launched`` /
  ``notify_game_stopped``) — bridging frontend-initiated
  launches onto the bus. These live in ``UIHandlers`` in the
  newer API.
* **Circuit breaker + log diagnostics** — same as
  ``LaunchHandlers``.

Difference vs ``LaunchHandlers``:

* ``get_launch_logs`` here reads from a module-level helper
  (``read_launch_logs``) rather than via the launch-history
  service — no service dependency, log files are read
  directly from disk.
"""

from __future__ import annotations

import time as _time
from typing import Any

from unifideck.core.types import Events
from unifideck.rpc import RpcError


class LaunchRPCMixin:
    """Launch lifecycle + circuit breaker + log diagnostics RPC."""

    bus: Any
    config: Any
    services: Any

    async def notify_game_launched(self, store: str, game_id: str, **kw: Any) -> Any:
        """Bridge a frontend-initiated launch onto the bus.

        When the user clicks Steam's "Play" button on a
        Unifideck shortcut, the dispatcher runs the game
        outside of ``LauncherService``. The frontend
        notifies via this method so playtime tracking +
        cloud-save sync etc. still observe ``GAME_LAUNCHED``.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            **kw: extra payload forwarded as event kwargs.

        Returns:
            ``{success: True}``.
        """
        await self.bus.emit(
            Events.GAME_LAUNCHED,
            store=store,
            game_id=game_id,
            **kw,
        )
        return {"success": True}

    async def notify_game_stopped(
        self,
        store: str,
        game_id: str,
        exit_code: int = 0,
    ) -> Any:
        """Bridge a frontend-detected game exit onto the bus.

        Counterpart to ``notify_game_launched``. The exit
        code defaults to 0 — the frontend may not know the
        real code if it only watches for window-close
        signals.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            exit_code: detected exit status.

        Returns:
            ``{success: True}``.
        """
        await self.bus.emit(
            Events.GAME_STOPPED,
            store=store,
            game_id=game_id,
            exit_code=exit_code,
        )
        return {"success": True}

    async def get_launch_failures(self, game_key: str) -> Any:
        """Return the rolling failure window + breaker state for a game.

        Combines four reads in a single call so the
        frontend renders the breaker panel without
        multiple round-trips.

        Args:
            game_key: ``"<store>:<game_id>"`` key.

        Returns:
            Dict with ``failures``, ``threshold``,
            ``window_seconds``, ``is_circuit_open``.
        """
        svc = self._require_launch_history()
        return {
            "failures": svc.get_recent_failures(game_key),
            "threshold": svc.threshold(),
            "window_seconds": svc.window_seconds(),
            "is_circuit_open": svc.is_circuit_open(game_key),
        }

    async def clear_launch_failures(self, game_key: str) -> Any:
        """Wipe every recent failure entry for ``game_key``.

        Closes the circuit immediately if it was open.
        Used by the "reset circuit" button.

        Args:
            game_key: ``"<store>:<game_id>"`` key.

        Returns:
            ``{success: True, game_key}``.
        """
        svc = self._require_launch_history()
        svc.clear_failures(game_key)
        return {"success": True, "game_key": game_key}

    async def arm_circuit_bypass(self, game_key: str) -> Any:
        """Arm a one-shot "try anyway" token for the next launch.

        Used by the "try anyway" button on the circuit-open
        toast. The token has a 5-minute TTL (enforced in
        the launch-history service).

        Args:
            game_key: ``"<store>:<game_id>"`` key.

        Returns:
            ``{success: True, game_key}``.
        """
        svc = self._require_launch_history()
        svc.arm_bypass(game_key)
        return {"success": True, "game_key": game_key}

    async def get_launch_logs(self, launch_id: str, max_lines: int = 500) -> Any:
        """Read the structured log for one launch attempt directly from disk.

        Differs from ``LaunchHandlers.get_launch_logs``
        which goes through the launch-history service —
        the mixin reads from disk via the diagnostics
        helper, so it works even when the launch-history
        service is unwired.

        Args:
            launch_id: correlation id from the launch
                attempt.
            max_lines: cap on returned lines (default 500).

        Returns:
            Dict from ``read_launch_logs`` (metadata +
            lines).
        """
        from unifideck.launcher.diagnostics.log_archive import read_launch_logs

        return read_launch_logs(
            launch_id,
            self.config,
            max_lines=max_lines,
        )

    async def export_launch_logs(self, launch_id: str, dest_path: str = "") -> Any:
        """Write the launch log archive to a user-visible file.

        Default destination: ``~/Downloads`` (configurable
        via ``logs.export_path``) with a timestamped
        filename. Explicit ``dest_path`` overrides.

        Defensive: if ``self.config`` is ``None`` (rare),
        fall back to ``~/Downloads`` literally rather than
        crashing.

        Args:
            launch_id: correlation id.
            dest_path: optional explicit destination path
                (empty → default scheme).

        Returns:
            Result dict from ``export_launch_logs``.
        """
        from unifideck.launcher.diagnostics.log_archive import export_launch_logs

        if not dest_path:
            export_root = (
                self.config.get_str("logs.export_path", "~/Downloads")
                if self.config
                else "~/Downloads"
            )
            ts = _time.strftime("%Y%m%d-%H%M%S")
            dest_path = f"{export_root}/unifideck-launch-{launch_id}-{ts}.log"
        return export_launch_logs(launch_id, dest_path, self.config)

    async def list_save_folder(
        self,
        store: str,
        game_id: str,
        max_depth: int = 2,
        filter_substring: str = "",
    ) -> Any:
        """List the cloud-save folder contents for a game (read-only).

        Used by the "inspect save folder" diagnostic.
        Depth cap (default 2) and per-folder file cap (500,
        hard-coded) bound the response size.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            max_depth: directory recursion depth.
            filter_substring: optional case-sensitive
                substring filter on file names.

        Returns:
            ``{store, game_id, **inspect_save_folder_result}``.

        Raises:
            RpcError: ``service_unavailable`` when the
                cloud-save service isn't wired.
        """
        svc = self.services.cloudsave
        if svc is None:
            raise RpcError(
                "service_unavailable",
                service="cloudsave",
            )
        root = svc.get_local_save_dir(store, game_id)
        from unifideck.launcher.diagnostics.save_folder_inspector import (
            inspect_save_folder,
        )

        result = inspect_save_folder(
            root,
            max_depth=max_depth,
            filter_substring=filter_substring,
            max_files=500,
        )
        return {"store": store, "game_id": game_id, **result}

    def _require_launch_history(self) -> Any:
        """Return the launch-history service or raise ``service_unavailable``.

        Returns:
            The ``LaunchHistoryService`` instance.

        Raises:
            RpcError: ``code="service_unavailable"``,
                ``service="launch_history"`` when missing.
        """
        svc = self.services.launch_history
        if svc is None:
            raise RpcError(
                "service_unavailable",
                service="launch_history",
            )
        return svc
