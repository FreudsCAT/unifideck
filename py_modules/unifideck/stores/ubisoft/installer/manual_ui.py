"""
UPC manual-UI driver — watches UPC windows and detects install completion.

OP-56e | py_modules/unifideck/stores/ubisoft/installer/manual_ui.py

UPC has no silent-install flag, so the installer must be driven by the
user pressing through the wizard. Once the wizard finishes UPC starts
a service-mode background loop which makes it hard to know when the
install is actually done.

This module exposes ``_ManualInstallDriver`` which:

* snapshots the ``drive_c/Program Files (x86)/.../games/`` directory
  *before* the install (``_snapshot_upc_game_dirs``);
* watches the parent of the install_base for new game-install dirs
  (``_check_new_dirs``);
* uses heuristics from ``looks_like_game_install`` to confirm the
  new directory really is a game install (not a transient temp dir).

Returns the detected install path or ``None`` on timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from unifideck.core.types import InstallResult
from unifideck.stores.ubisoft.config import UbisoftConfig
from unifideck.stores.ubisoft.id_map import UbisoftIdMap
from unifideck.stores.ubisoft.library import UbisoftLibrary
from unifideck.stores.ubisoft.library.detection_helpers import looks_like_game_install
from unifideck.stores.ubisoft.session import UbisoftSession

from . import registry as _reg
from .window_probe import upc_window_visible

logger = logging.getLogger(__name__)
_MANUAL_INSTALL_TIMEOUT_S = 2 * 60 * 60
_MANUAL_INSTALL_POLL_INTERVAL_S = 10.0
_STABILITY_WAIT_MAX_POLLS = 360
_STABILITY_POLL_INTERVAL_S = 10.0
_STABILITY_STABLE_THRESHOLD = 3
# Consecutive polls (× _MANUAL_INSTALL_POLL_INTERVAL_S = ~3min) with the UPC
# window gone — after it was seen once — before we treat the session as
# abandoned. Generous on purpose: it must outlast the first-run
# installer→main-launcher window handoff so we never end a real install early.
# Abandonment is additionally gated on the UPC process having exited (see
# ``_upc_process_alive``), so this threshold is now only a backstop for when
# that liveness probe can't run.
_UPC_WINDOW_GONE_THRESHOLD = 18


class _ManualUiInstaller:
    """Manual UI installer."""

    def __init__(
        self,
        config: UbisoftConfig,
        library: UbisoftLibrary,
        id_map: UbisoftIdMap,
        session: UbisoftSession,
        active_install_pids: dict[str, int],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._library = library
        self._id_map = id_map
        self._session = session
        self._active_install_pids = active_install_pids

    async def install_via_upc_ui(
        self,
        *,
        game_id: str,
        game_name: str | None,
        prefix_path: str,
        env: dict[str, str],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
        install_path: str | None,
        on_ready: Callable[[], Awaitable[None]] | None = None,
    ) -> InstallResult:
        """Drive a UPC install whose window is opened by the frontend.

        UPC is launched by the frontend through Steam's ``RunGame`` (so it
        gets its own gamescope/XWayland session and actually renders in
        Gaming Mode). This method only prepares the prefix, signals
        ``on_ready`` — from which the worker asks the frontend to RunGame
        UPC — and then watches the prefix for the installed game. There is
        no backend UPC process to hold: the install is done when the game
        files appear and stabilise; teardown/cancel ``pkill``s upc.exe,
        which works regardless of which gamescope session it lives in.
        """
        logger.info(
            "[UbisoftInstaller] preparing manual UPC install for %s",
            game_id,
        )
        self._session.inject_into_prefix(prefix_path)
        install_base, dirs_before, upc_dirs_before = self._snapshot_pre_install(
            install_path, prefix_path
        )
        await self._notify_upc_launching(progress_cb)
        if on_ready is not None:
            await on_ready()
        try:
            install_dir = await self._poll_for_new_install(
                install_base=install_base,
                dirs_before=dirs_before,
                upc_dirs_before=upc_dirs_before,
                env=env,
                progress_cb=progress_cb,
            )
        except asyncio.CancelledError:
            # Explicit cancel from the download queue is the ONLY path that
            # closes UPC. The completion/timeout paths must NOT: completion is
            # inferred from the install dir's size holding steady for ~30s, so
            # a mid-download pause (UPC verifying/extracting a chunk, a network
            # stall, a phase transition) can look "done" — and killing UPC then
            # interrupts a still-running install (the user saw UPC close
            # mid-install and resume on reopen). On completion we leave UPC
            # open; it trays / the user closes it. ``_pkill_upc`` runs
            # synchronously before the ``raise`` re-raises during the cancel
            # unwind, and closes the launcher even across its RunGame gamescope
            # session.
            self._pkill_upc()
            raise
        finally:
            self._active_install_pids.pop(game_id, None)
            # Capture a fresh/rotated UPC token from this prefix back to the
            # auth prefix on every exit path (incl. the cancel unwind).
            # Otherwise a token UPC rotated this run is lost and the next
            # install/launch injects the stale auth-prefix credential → UPC
            # opens logged out. capture() is guarded (acts only on a valid,
            # non-logged-out credential), so a half-written session is ignored.
            self._capture_and_propagate_session(prefix_path)
        if not install_dir:
            return InstallResult(
                success=False,
                store="ubisoft",
                game_id=game_id,
                error="no_install_detected",
            )
        return await self._finalize_manual_install(
            game_id=game_id,
            game_name=game_name,
            install_dir=install_dir,
            prefix_path=prefix_path,
        )

    async def _notify_upc_launching(
        self,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Emit the indeterminate "UPC is opening" manual-phase update.

        No longer spawns UPC — the frontend opens it via RunGame after
        ``on_ready``. The worker maps ``phase``→download_phase and
        ``phase_message``→phase_message, and the frontend renders an
        indeterminate "Installing in Ubisoft Connect" state (no
        %/speed/ETA).
        """
        if progress_cb:
            await progress_cb(
                {
                    "phase": "manual",
                    "phase_message": (
                        "Ubisoft Connect is opening — install the "
                        "game from the launcher window."
                    ),
                }
            )
        logger.info(
            "[UbisoftInstaller] awaiting UPC launch via frontend RunGame",
        )

    def _snapshot_pre_install(
        self,
        install_path: str | None,
        prefix_path: str,
    ) -> tuple[str, set[str], dict[str, set[str]]]:
        """Snapshot pre install."""
        install_base, dirs_before = self._snapshot_install_base(
            install_path,
        )
        upc_dirs_before = self._snapshot_upc_game_dirs(prefix_path)
        return install_base, dirs_before, upc_dirs_before

    def _capture_and_propagate_session(
        self,
        prefix_path: str,
    ) -> None:
        """Capture and propagate session."""
        if self._session.capture(prefix_path):
            self._session.propagate_all_to_all()

    def _snapshot_install_base(
        self,
        install_path: str | None,
    ) -> tuple[str, set[Any]]:
        """Snapshot install base."""
        install_base = install_path or self._config.default_install_base_expanded
        Path(install_base).mkdir(parents=True, exist_ok=True)
        dirs_before: set[Any] = set()
        with contextlib.suppress(OSError):
            dirs_before = {entry.name for entry in Path(install_base).iterdir()}
        return install_base, dirs_before

    @staticmethod
    def _pkill_upc() -> None:
        """Kill the actual UPC Wine processes (the launcher window).

        Terminating the ``python umu-run upc.exe`` wrapper does NOT close
        the UPC window — ``upc.exe`` runs as a Wine descendant under
        wineserver and survives. ``pkill -f`` on the Wine image names is
        how the rest of the codebase (``UbisoftInstaller.kill_upc_processes``)
        reliably closes it, so cancel actually shuts the launcher.
        """
        import subprocess
        for pattern in ("upc.exe", "UbisoftConnect.exe"):
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["pkill", "-f", pattern],
                    capture_output=True,
                    timeout=5,
                    check=False,  # rc=1 on "no match" is expected
                )

    @staticmethod
    def _upc_process_alive() -> bool:
        """Whether a UPC Wine process is currently running.

        Counterpart to ``_pkill_upc`` (same image names, ``pgrep -f``
        instead of ``pkill -f``). The window-gone watchdog uses this to
        tell "the user quit UPC" (process gone → abandon the install)
        from "UPC minimized to the tray" (process alive → keep waiting):
        ``--onlyvisible`` reports a tray'd window as not visible, which
        would otherwise look identical to a quit. ``pgrep`` returns rc=0
        when a match exists, rc=1 on no match; a probe error (pgrep
        missing) returns ``True`` so a broken probe can never end a real
        install — same fail-safe spirit as ``upc_window_visible``.
        """
        import subprocess
        for pattern in ("upc.exe", "UbisoftConnect.exe"):
            try:
                result = subprocess.run(
                    ["pgrep", "-f", pattern],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return True  # can't check → assume alive (never abort)
            if result.returncode == 0:
                return True
        return False

    async def _finalize_manual_install(
        self,
        *,
        game_id: str,
        game_name: str | None,
        install_dir: str,
        prefix_path: str,
    ) -> InstallResult:
        """Finalize manual install."""
        exe = self._library.find_game_executable(install_dir)
        await self._library.write_install_marker(
            space_id=game_id,
            install_path=install_dir,
            executable=exe or "",
            game_title=game_name or "",
        )
        final_size = _reg.get_directory_size(install_dir)
        logger.info(
            "[UbisoftInstaller] manual install complete: %s (%.0f MB)",
            install_dir,
            final_size / (1024 * 1024),
        )
        await self._seed_launch_id(game_id, prefix_path, game_name)
        return InstallResult(
            success=True,
            store="ubisoft",
            game_id=game_id,
            install_path=install_dir,
            size_bytes=final_size,
            metadata={"executable": exe},
        )

    def _launch_id_ok(self, game_id: str) -> bool:
        """Whether a usable (non-zero) uplay launch id resolves for a game."""
        resolved = self._id_map.resolve_launch_id(game_id)
        return bool(resolved) and str(resolved) != "0"

    async def _seed_launch_id(
        self,
        game_id: str,
        prefix_path: str,
        game_name: str | None,
    ) -> None:
        """Make sure a uplay launch id is resolvable right after install.

        The launcher builds ``uplay://launch/{id}/0`` from
        ``ubisoft_id_map.json``; with no resolvable id, Play can't launch the
        game directly (it opens UPC bare). UPC writes the config files we read
        from asynchronously, so the configuration refresh can miss on the
        first pass — fall back to the Wine registry, then a unifiDB name
        lookup (mirrors the library detector's ``_auto_resolve_missing_id``).
        Best-effort: a failure here only costs direct-launch, never the
        install itself.
        """
        try:
            await self._id_map.refresh_from_configurations(game_id)
        except Exception as e:
            logger.warning(
                "[UbisoftInstaller] id_map refresh after install failed: %s",
                e,
            )
        if self._launch_id_ok(game_id):
            return
        reg_id = self._id_map.extract_game_id_from_registry(prefix_path)
        if not reg_id and game_name:
            with contextlib.suppress(Exception):
                reg_id = await self._id_map.lookup_game_id_by_name(game_name)
        if reg_id:
            self._id_map.merge_entry(
                game_id,
                {
                    "install_id": reg_id,
                    "launch_id": reg_id,
                    "ubisoftconnect_game_id": reg_id,
                    "name": game_name or "",
                },
            )
            logger.info(
                "[UbisoftInstaller] seeded uplay launch id for %s: %s",
                game_id, reg_id,
            )
            return
        logger.warning(
            "[UbisoftInstaller] could not resolve a uplay launch id for %s — "
            "Play will open Ubisoft Connect until a library sync seeds it",
            game_id,
        )

    @staticmethod
    def _snapshot_upc_game_dirs(
        prefix_path: str,
    ) -> dict[str, set[Any]]:
        """Snapshot UPC game dirs.

        ALWAYS records both candidate ``games/`` dirs — with an empty
        baseline when the dir doesn't exist yet. On a fresh prefix the
        ``games/`` dir is created by UPC only once the install starts, so
        the previous "only if ``is_dir``" guard left it unwatched and the
        newly-installed game was never detected (→ false ``no_install_detected``
        even though the game installed fine). With an empty baseline, the
        game folder that appears under it is correctly seen as new.
        """
        upc_games_rel = str(Path("drive_c") / "Program Files (x86)" / "Ubisoft" / "Ubisoft Game Launcher" / "games")
        candidates = (
            str(Path(prefix_path) / upc_games_rel),
            str(Path(prefix_path) / "pfx" / upc_games_rel),
        )
        snapshots: dict[str, set[Any]] = {}
        for gdir in candidates:
            try:
                snapshots[gdir] = {entry.name for entry in Path(gdir).iterdir()}
            except OSError:
                # Dir doesn't exist yet — watch it with an empty baseline so
                # the first game folder created under it counts as new.
                snapshots[gdir] = set()
        return snapshots

    async def _poll_for_new_install(
        self,
        *,
        install_base: str,
        dirs_before: set[Any],
        upc_dirs_before: dict[str, set[Any]],
        env: dict[str, str],
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> str | None:
        """Poll until a new install directory appears or UPC goes away.

        UPC is launched by the frontend (RunGame), so there's no backend
        process handle to watch. Exit conditions:
          1. a game dir appears (success) — see ``_detect_new_install``;
          2. the UPC *window* disappears for ~60s after having been seen
             (``_window_gone``) — the user closed it. NOTE: the window
             probe (``xdotool``) only works when UPC shares the backend's
             X server (Desktop Mode); in Gaming Mode UPC has its own
             gamescope/XWayland session, so the probe returns "unknown"
             and this signal never fires — the install then ends only on
             (1), the overall timeout, or an explicit Cancel.

        Refactor history (2026-05-14): the original implementation
        nested the "is there a new dir anywhere" check three
        levels deep (main install_base → UPC fallback loop → match
        test) and inlined the periodic-progress emit at the same
        level as the exit-detection branch. CC was 17. Pulled the
        detection sweep and the progress tick into helpers so the
        loop body reads as a flat ``detect → react → tick``.
        """
        install_dir: str | None = None
        max_polls = int(
            _MANUAL_INSTALL_TIMEOUT_S / _MANUAL_INSTALL_POLL_INTERVAL_S,
        )
        window_ever_seen = False
        no_window_polls = 0
        for iteration in range(max_polls):
            await asyncio.sleep(
                _MANUAL_INSTALL_POLL_INTERVAL_S,
            )
            install_dir = self._detect_new_install(
                install_base, dirs_before, upc_dirs_before,
            )
            if install_dir:
                logger.info(
                    "[UbisoftInstaller] detected install at %s",
                    install_dir,
                )
                await self._notify_install_detected(
                    install_dir,
                    progress_cb,
                )
                await self._wait_for_install_completion(
                    install_dir,
                    progress_cb,
                )
                return install_dir
            window_ever_seen, no_window_polls = self._track_window_presence(
                env, window_ever_seen, no_window_polls,
            )
            if window_ever_seen and no_window_polls >= _UPC_WINDOW_GONE_THRESHOLD:
                # The window's been gone a while — but ``--onlyvisible`` also
                # reports a UPC minimized to the tray as "not visible", which
                # is exactly what happens during a long download. Only treat
                # the session as abandoned when UPC's *process* is also gone
                # (the user actually quit it). If it's still running, it's
                # just backgrounded — reset the counter and keep waiting so we
                # never kill an in-progress install.
                if self._upc_process_alive():
                    logger.debug(
                        "[UbisoftInstaller] UPC window gone for %d polls but "
                        "the process is still alive (minimized/tray) — "
                        "continuing to wait",
                        no_window_polls,
                    )
                    no_window_polls = 0
                else:
                    logger.info(
                        "[UbisoftInstaller] UPC window gone for %d polls "
                        "(~%.0fs) and the process has exited — treating "
                        "install session as abandoned",
                        no_window_polls,
                        no_window_polls * _MANUAL_INSTALL_POLL_INTERVAL_S,
                    )
                    return None
            await self._maybe_emit_waiting_tick(progress_cb, iteration)
        return None

    def _track_window_presence(
        self,
        env: dict[str, str],
        window_ever_seen: bool,
        no_window_polls: int,
    ) -> tuple[bool, int]:
        """Advance the UPC-window-visibility tracker by one poll.

        Returns the updated ``(window_ever_seen, no_window_polls)``. A
        ``None`` probe result (xdotool missing / no DISPLAY / error) is
        treated as "unknown" and leaves the counters untouched, so a probe
        failure can NEVER end a real install — the feature simply no-ops in
        environments where the window can't be queried.
        """
        visible = upc_window_visible(env)
        if visible is True:
            if not window_ever_seen:
                logger.info("[UbisoftInstaller] UPC window detected (foreground)")
            return True, 0
        if visible is False and window_ever_seen:
            return window_ever_seen, no_window_polls + 1
        return window_ever_seen, no_window_polls

    # ─────────────────────────────────────────────────────────────
    # Helpers extracted from the former CC=17 _poll_for_new_install
    # ─────────────────────────────────────────────────────────────

    def _detect_new_install(
        self,
        install_base: str,
        dirs_before: set[Any],
        upc_dirs_before: dict[str, set[Any]],
    ) -> str | None:
        """Probe every watched directory for a new install dir.

        Two locations are watched in priority order :

            1. The user-configured ``install_base`` (the path
               we asked UPC to use).
            2. UPC's per-prefix ``games`` directories — fallback
               for the case where UPC overrides ``install_base``
               and drops the game in its default folder anyway.

        Returns the *first* match found (priority preserved) or
        ``None`` if nothing showed up since the snapshot.
        """
        install_dir = self._check_new_dirs(install_base, dirs_before)
        if install_dir:
            return install_dir
        for gdir, before in upc_dirs_before.items():
            found = self._check_new_dirs(gdir, before)
            if found:
                return found
        return None

    @staticmethod
    async def _maybe_emit_waiting_tick(
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
        iteration: int,
    ) -> None:
        """Emit a "still waiting" progress tick every 6 iterations.

        At ``_MANUAL_INSTALL_POLL_INTERVAL_S = 10s``, every 6
        iterations is ~1 minute — enough to keep the UI alive
        without spamming the bus on every poll. Silent no-op
        when no progress callback is wired.
        """
        if not progress_cb or iteration % 6 != 0:
            return
        await progress_cb(
            {
                "phase": "manual",
                "phase_message": (
                    "Waiting for game installation in Ubisoft Connect…"
                ),
            }
        )

    @staticmethod
    async def _notify_install_detected(
        install_dir: str,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Notify install detected."""
        if not progress_cb:
            return
        await progress_cb(
            {
                "phase": "manual",
                "phase_message": (
                    f"Installing {Path(install_dir).name} via Ubisoft Connect…"
                ),
            }
        )

    def _check_new_dirs(
        self,
        base: str,
        before: set[Any],
    ) -> str | None:
        """Check new dirs."""
        try:
            now = {entry.name for entry in Path(base).iterdir()}
        except OSError:
            return None
        new_dirs = now - before
        for d in new_dirs:
            candidate = str(Path(base) / d)
            if Path(candidate).is_dir() and looks_like_game_install(candidate):
                return candidate
        return None

    async def _wait_for_install_completion(
        self,
        install_dir: str,
        progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Wait for install completion."""
        prev_size = 0
        stable_count = 0
        for _ in range(_STABILITY_WAIT_MAX_POLLS):
            await asyncio.sleep(_STABILITY_POLL_INTERVAL_S)
            curr_size = _reg.get_directory_size(install_dir)
            # Stability detection (mirrors staging's correct structure):
            # increment ``stable_count`` while the size is unchanged and
            # non-zero, reset it whenever the size changes, and ALWAYS
            # advance ``prev_size`` afterwards. The for-pr-0.7 refactor
            # updated ``prev_size`` only inside the equality branch and
            # reset ``stable_count`` right after incrementing — so the
            # size never "stabilised" and completion only fired after the
            # full timeout (~1h).
            if curr_size == prev_size and curr_size > 0:
                stable_count += 1
                if stable_count >= _STABILITY_STABLE_THRESHOLD:
                    break
            else:
                stable_count = 0
            prev_size = curr_size
            if progress_cb and curr_size > 0:
                await progress_cb(
                    {
                        "phase": "manual",
                        "phase_message": (
                            f"Installing… ({curr_size / (1024**3):.1f} GB)"
                        ),
                    }
                )
