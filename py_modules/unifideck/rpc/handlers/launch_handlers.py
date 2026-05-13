"""LaunchHandlers — launch-history + circuit breaker + playtime RPC.

OP-25d | py_modules/unifideck/rpc/handlers/launch_handlers.py

Surfaces three concerns to the frontend:

* **Circuit breaker** — read recent failures, clear them,
  arm a one-shot bypass token.
* **Launch logs** — read the structured logs of a specific
  launch attempt, optionally export to disk.
* **Playtime** — read per-game and global playtime stats.

The launch-history and playtime services are both optional in
the container; ``_ensure_launch_history`` and ``_require``
surface a typed ``service_unavailable`` error when missing.
"""

from __future__ import annotations

from typing import Any, cast

from unifideck.rpc.handlers.base import RpcHandlerBase
from unifideck.rpc.wrapper import RpcError


class LaunchHandlers(RpcHandlerBase):
    """Launch-history, circuit-breaker, log-export, playtime RPC."""

    def _ensure_launch_history(self):
        """Return the launch-history service or raise ``service_unavailable``.

        Slight variant of ``_require``: keeps the type
        annotation implicit (``Any``) since the methods that
        use this don't need explicit type narrowing past
        what mypy infers from the assignment.

        Returns:
            The ``LaunchHistoryService`` instance.

        Raises:
            RpcError: ``code="service_unavailable"``,
                ``service="launch_history"``.
        """
        svc = self._services.launch_history
        if svc is None:
            raise RpcError(
                "service_unavailable",
                service="launch_history",
            )
        return svc

    async def get_launch_failures(self, game_key: str) -> Any:
        """Return the rolling failure window + breaker state for a game.

        Combines four reads in a single call so the frontend
        renders the circuit-breaker panel without making
        multiple round-trips.

        Args:
            game_key: ``"<store>:<game_id>"`` key.

        Returns:
            Dict with ``failures`` (list of recent failure
            dicts), ``threshold``, ``window_seconds``,
            ``is_circuit_open`` (bool).
        """
        svc = self._ensure_launch_history()
        return {
            "failures": svc.get_recent_failures(game_key),
            "threshold": svc.threshold(),
            "window_seconds": svc.window_seconds(),
            "is_circuit_open": svc.is_circuit_open(game_key),
        }

    async def clear_launch_failures(self, game_key: str) -> Any:
        """Wipe every recent failure entry for ``game_key``.

        Closes the circuit immediately if it was open. Used
        by the "reset circuit" button on the failures panel.

        Args:
            game_key: ``"<store>:<game_id>"`` key.

        Returns:
            ``{success: True, game_key}``.
        """
        svc = self._ensure_launch_history()
        svc.clear_failures(game_key)
        return {"success": True, "game_key": game_key}

    async def arm_circuit_bypass(self, game_key: str) -> Any:
        """Arm a one-shot "try anyway" token for the next launch.

        Used by the "try anyway" button on the circuit-open
        toast. The token has a 5-minute TTL (enforced in the
        launch-history service); the next launch consumes it
        atomically.

        Args:
            game_key: ``"<store>:<game_id>"`` key.

        Returns:
            ``{success: True, game_key}``.
        """
        svc = self._ensure_launch_history()
        svc.arm_bypass(game_key)
        return {"success": True, "game_key": game_key}

    async def get_launch_logs(self, launch_id: str, max_lines: int = 500) -> Any:
        """Read the structured log for one launch attempt.

        Delegates to the launch-history service's log reader
        which walks the per-launch log directory and returns
        a parsed structure (metadata + lines).

        Args:
            launch_id: correlation id from the launch
                attempt.
            max_lines: cap on returned lines (default 500
                — enough for the diagnostic UI without
                overwhelming the RPC payload).

        Returns:
            Dict from ``read_launch_log`` (shape determined
            by the service).
        """
        svc = self._ensure_launch_history()
        return cast(dict, svc.read_launch_log(launch_id, max_lines=max_lines))

    async def export_launch_logs(self, launch_id: str, dest_path: str = "") -> Any:
        """Write the launch log archive to a user-visible file.

        Default destination: ``~/Downloads`` (configurable
        via ``logs.export_path``) with a timestamped filename
        ``unifideck-launch-<id>-YYYYmmdd-HHMMSS.log``.
        Explicit ``dest_path`` overrides both.

        Used by the "export logs" button on the diagnostics
        panel — the user then attaches the file to a bug
        report.

        Args:
            launch_id: correlation id.
            dest_path: optional explicit destination path
                (empty string → use the default scheme).

        Returns:
            Result dict from ``export_launch_logs``
            (shape determined by the diagnostics helper).
        """
        import time

        from unifideck.launcher.diagnostics.log_archive import export_launch_logs

        if not dest_path:
            export_root = self._config.get_str(
                "logs.export_path",
                "~/Downloads",
            )
            ts = time.strftime("%Y%m%d-%H%M%S")
            dest_path = f"{export_root}/unifideck-launch-{launch_id}-{ts}.log"
        return export_launch_logs(launch_id, dest_path, self._config)

    async def list_save_folder(
        self,
        store: str,
        game_id: str,
        max_depth: int = 2,
        filter_substring: str = "",
    ) -> Any:
        """List the cloud-save folder contents for a game (read-only).

        Used by the "inspect save folder" diagnostic to
        let the user check what files are being synced.
        Requires the cloud-save service (raises
        ``service_unavailable`` if missing).

        The depth cap (default 2) and per-folder file cap
        (500, hard-coded) bound the response size — useful
        for games with deeply-nested save trees.

        Args:
            store: store identifier.
            game_id: store-specific game id.
            max_depth: directory recursion depth.
            filter_substring: optional case-sensitive
                substring filter on file names.

        Returns:
            ``{store, game_id, **inspect_save_folder_result}``.
        """
        from unifideck.launcher.diagnostics.save_folder_inspector import (
            inspect_save_folder,
        )

        svc = self._services.cloudsave
        if svc is None:
            raise RpcError("service_unavailable", service="cloudsave")
        root = svc.get_local_save_dir(store, game_id)
        result = inspect_save_folder(
            root,
            max_depth=max_depth,
            filter_substring=filter_substring,
            max_files=500,
        )
        return {"store": store, "game_id": game_id, **result}

    async def get_playtime(self, store: str, game_id: str) -> Any:
        """Return aggregated playtime for one game.

        Delegates to ``PlaytimeService.get_playtime`` which
        produces ``{total_seconds, session_count, last_played}``
        — see OP-18a.

        Args:
            store: store identifier.
            game_id: store-specific game id.

        Returns:
            Per-game playtime dict.
        """
        svc = self._require(self._services.playtime, "playtime")
        return cast(dict, await svc.get_playtime(store, game_id))

    async def get_all_playtimes(self) -> Any:
        """Return aggregated playtime for every tracked game.

        Used by the "recently played" UI tile.

        Returns:
            List of per-game playtime dicts ordered by
            ``last_played`` descending.
        """
        svc = self._require(self._services.playtime, "playtime")
        return cast(dict, await svc.get_all_playtimes())
