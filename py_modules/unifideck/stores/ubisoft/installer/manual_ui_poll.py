"""
UPC install-detection / polling helpers for the manual-UI driver.

OP-56e | py_modules/unifideck/stores/ubisoft/installer/manual_ui_poll.py

Split out of ``manual_ui.py`` (like ``window_probe.py`` before it) to keep
that module under the volumetry file-size cap and focused on the install
*orchestration*. This module owns the "watch the prefix / install_base until a
new game directory appears (or the user quits UPC)" loop and its helpers.

``_ManualUiPollMixin`` is mixed into ``_ManualUiInstaller``; every method here
runs against the composed installer instance, so it can call installer-owned
helpers (e.g. ``_upc_process_alive``) via normal ``self`` dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.stores.ubisoft.library.detection_helpers import looks_like_game_install

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
# Consecutive polls (× 10s = ~90s) at the START of a manual install with NO
# UPC process running, before we conclude it never launched.
#
# The window-gone watchdog above cannot cover this: it is gated on
# ``window_ever_seen``, so when UPC never appears at all that flag stays
# False and the loop runs the full two-hour timeout showing "INSTALLING
# UBISOFT CONNECT / Follow the Ubisoft Connect window" — indistinguishable
# from a hang, with nothing in the log after "awaiting UPC launch".
#
# That is a real field failure and not a hypothetical: the frontend's RunGame
# died instantly with ``GameNotFoundError: game 'ubisoft:80' not found in
# games.map`` (a prefix-detection bug, fixed separately in
# ``dispatcher._ubisoft_has_populated_prefix``), so UPC was never spawned and
# the install sat there. Any future cause — a dropped launch option, a
# missing shortcut, Steam refusing RunGame — produces the same shape, so the
# poll loop needs its own answer rather than trusting the launch to succeed.
#
# Only the PROCESS is consulted, never the window probe: the probe cannot see
# into Gaming Mode's separate gamescope session, whereas ``pgrep`` works in
# both. 90s is comfortably longer than a cold UPC start under Proton while
# still failing fast enough to be actionable.
_UPC_NEVER_STARTED_THRESHOLD = 9


class _ManualUiPollMixin:
    """Prefix/install-base watching for the manual UPC installer."""

    if TYPE_CHECKING:
        # Provided by the composing ``_ManualUiInstaller`` (stays in
        # ``manual_ui.py`` alongside ``_pkill_upc``). Declared here so mypy
        # resolves the reference in ``_handle_window_gone`` without a runtime
        # body that could shadow the real implementation.
        def _upc_process_alive(self) -> bool: ...

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

        The per-poll body reads as a flat ``detect → react → tick``: the
        detection sweep (``_detect_new_install``), the window-gone
        abandonment decision (``_handle_window_gone``) and the periodic
        progress emit (``_maybe_emit_waiting_tick``) are each factored out.
        """
        install_dir: str | None = None
        max_polls = int(
            _MANUAL_INSTALL_TIMEOUT_S / _MANUAL_INSTALL_POLL_INTERVAL_S,
        )
        window_ever_seen = False
        no_window_polls = 0
        never_started_polls = 0
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
            counters, abandon = self._check_abandonment(
                window_ever_seen, no_window_polls, never_started_polls,
            )
            if abandon:
                return None
            no_window_polls, never_started_polls = counters
            await self._maybe_emit_waiting_tick(progress_cb, iteration)
        return None

    def _check_abandonment(
        self,
        window_ever_seen: bool,
        no_window_polls: int,
        never_started_polls: int,
    ) -> tuple[tuple[int, int], bool]:
        """Run both give-up watchdogs for one poll.

        Returns ``((no_window_polls, never_started_polls), abandon)``.

        The two cover disjoint situations and neither substitutes for the
        other: ``_handle_window_gone`` catches "UPC was up and the user quit
        it", while ``_track_upc_never_started`` catches "UPC never came up at
        all" — the case that otherwise burned the full two-hour timeout
        because the window-gone path is gated on having seen a window.
        """
        if not window_ever_seen:
            never_started_polls, give_up = self._track_upc_never_started(
                never_started_polls,
            )
            return (no_window_polls, never_started_polls), give_up
        if no_window_polls >= _UPC_WINDOW_GONE_THRESHOLD:
            abandon, no_window_polls = self._handle_window_gone(no_window_polls)
            return (no_window_polls, never_started_polls), abandon
        return (no_window_polls, never_started_polls), False

    def _handle_window_gone(self, no_window_polls: int) -> tuple[bool, int]:
        """Decide whether a long-gone UPC window means the install was abandoned.

        ``--onlyvisible`` also reports a UPC minimized to the tray as "not
        visible", which is exactly what happens during a long download. Only
        treat the session as abandoned when UPC's *process* is also gone (the
        user actually quit it). If it's still running, it's just backgrounded —
        reset the counter and keep waiting so we never kill an in-progress
        install.

        Returns ``(abandon, updated_no_window_polls)``.
        """
        if self._upc_process_alive():
            logger.debug(
                "[UbisoftInstaller] UPC window gone for %d polls but "
                "the process is still alive (minimized/tray) — "
                "continuing to wait",
                no_window_polls,
            )
            return False, 0
        logger.info(
            "[UbisoftInstaller] UPC window gone for %d polls "
            "(~%.0fs) and the process has exited — treating "
            "install session as abandoned",
            no_window_polls,
            no_window_polls * _MANUAL_INSTALL_POLL_INTERVAL_S,
        )
        return True, no_window_polls

    def _track_upc_never_started(
        self, never_started_polls: int,
    ) -> tuple[int, bool]:
        """Count consecutive early polls with no UPC process at all.

        Returns ``(updated_count, give_up)``. Called only while the UPC
        window has never been seen, so it stops mattering the moment UPC
        actually shows up.

        See :data:`_UPC_NEVER_STARTED_THRESHOLD` for why this exists: the
        window-gone watchdog cannot detect "never launched", so without this
        the install waits the full two-hour timeout with no diagnosis.

        A live process resets the counter — a cold UPC start under Proton is
        slow, and this must never cut short an install that is merely still
        coming up.
        """
        if self._upc_process_alive():
            return 0, False
        never_started_polls += 1
        if never_started_polls < _UPC_NEVER_STARTED_THRESHOLD:
            return never_started_polls, False
        waited = int(
            _UPC_NEVER_STARTED_THRESHOLD * _MANUAL_INSTALL_POLL_INTERVAL_S,
        )
        logger.error(
            "[UbisoftInstaller] Ubisoft Connect never started (%ds with no "
            "upc.exe process). The install cannot proceed — it was waiting "
            "for a UPC window that will never appear. Check the launcher log "
            "for this game: a failed RunGame (e.g. the title missing from "
            "games.map) leaves exactly this state.",
            waited,
        )
        return never_started_polls, True

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
