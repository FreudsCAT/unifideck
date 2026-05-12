"""Launch-history service — track per-game launch outcomes for a circuit breaker.

OP-21a | py_modules/unifideck/services/launch_history/service.py

``LaunchHistoryService`` is the orchestration class composed from
two mixins:

* ``_FailuresMixin`` — record successes / failures, query recent
  failures, decide whether the circuit is open (a game that's been
  crashing repeatedly within the rolling window is "circuit open" =
  refuse-to-launch);
* ``_BypassMixin``   — one-shot user-triggered overrides for the
  open-circuit decision (user clicks "try anyway" in the QAM).

The service subscribes to ``GAME_STOPPED`` and uses ``exit_code`` +
``elapsed_seconds`` to decide whether the launch was a "fast boot
failure" (game crashed within N seconds of being launched). Other
outcomes (clean exit, signal termination, long-running session that
later crashed) are not counted as failures by this service — they
have their own categories and aren't typically circuit-breaker
worthy.

State is persisted to ``~/.local/share/unifideck/launch_history.json``
so the circuit-breaker decision survives plugin restarts.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ...core.types.events import Events
from ...event_bus.event_bus_devex import subscribe
from .bypass import _BypassMixin
from .config_readers import (
    DEFAULT_FAST_BOOT_SECONDS,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_SECONDS,
    read_fast_boot_seconds,
    read_threshold,
    read_window_seconds,
)
from .constants import FAILURE_KIND_FAST_BOOT
from .failures import _FailuresMixin

logger = logging.getLogger(__name__)


class LaunchHistoryService(_FailuresMixin, _BypassMixin):
    """Per-game failure tracker driving a launch circuit breaker."""

    DEFAULT_THRESHOLD = DEFAULT_THRESHOLD
    DEFAULT_WINDOW_SECONDS = DEFAULT_WINDOW_SECONDS
    DEFAULT_FAST_BOOT_SECONDS = DEFAULT_FAST_BOOT_SECONDS

    def __init__(
        self,
        config: Any | None = None,
        storage_path: Path | None = None,
        bus: Any | None = None,
    ) -> None:
        """Wire the service to its config and storage backend.

        Args:
            config: optional config manager. The three tunables
                read are ``launch_history.threshold``,
                ``launch_history.window_seconds``,
                ``launch_history.fast_boot_seconds`` — all with
                hard-coded defaults if absent.
            storage_path: optional override for the JSON persistence
                file path. Defaults to
                ``~/.local/share/unifideck/launch_history.json``
                when ``None`` (production) — overridable for
                tests to avoid touching the real user state.
            bus: optional event bus. When present, the service
                emits ``CIRCUIT_STATE_CHANGED`` whenever the circuit
                breaker flips for a game. When absent (tests),
                state changes still happen but no event is emitted.
        """
        self._config = config
        if storage_path is None:
            storage_path = (
                Path.home() / ".local" / "share" / "unifideck" / "launch_history.json"
            )
        self._path = Path(storage_path)
        self._bus = bus

    def threshold(self) -> int:
        """Return the failure-count threshold that opens the circuit.

        Read fresh from the config each call so a runtime config
        change takes immediate effect on the next launch decision.

        Returns:
            Number of failures within the rolling window required
            to open the circuit (default 3).
        """
        return read_threshold(self._config)

    def window_seconds(self) -> float:
        """Return the rolling-window length used for failure counting.

        Returns:
            Window length in seconds (default 3600 = 1 hour).
            Failures older than ``now - window_seconds`` are
            ignored when checking the threshold.
        """
        return read_window_seconds(self._config)

    def fast_boot_seconds(self) -> float:
        """Return the fast-boot-failure cutoff.

        A launch that exits with non-zero status in less than
        this many seconds is classified as a fast boot failure
        (likely a crash on startup, not a clean shutdown).

        Returns:
            Cutoff in seconds (default 30).
        """
        return read_fast_boot_seconds(self._config)

    def _emit_state(self, game_key: str, trigger: str) -> None:
        """Emit ``CIRCUIT_STATE_CHANGED`` if a bus is configured.

        Called by the failure / bypass mixins after any state
        change that may have flipped the circuit. The emission is
        scheduled via ``loop.create_task`` so the caller (which is
        synchronous: ``record_failure``, ``record_success``) doesn't
        need to be awaitable.

        Failures during emission are logged at WARN and swallowed —
        the circuit-breaker state has already been mutated, so a
        failed event emission is not worth aborting the call for.

        Args:
            game_key: the ``"<store>:<game_id>"`` key whose state
                changed.
            trigger: short label identifying what caused the
                change (``"failure"``, ``"success"``, ``"bypass"``,
                etc.) — surfaced in the event payload for
                debugging.
        """
        if self._bus is None:
            return
        try:
            recent = self.get_recent_failures(game_key)
            payload = {
                "game_key": game_key,
                "state": "open" if self.is_circuit_open(game_key) else "closed",
                "recent_count": len(recent),
                "failure_kinds": [f.get("kind", "unknown") for f in recent],
                "trigger": trigger,
            }
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._bus.emit(
                        Events.CIRCUIT_STATE_CHANGED,
                        **payload,
                    ),
                    name=f"circuit-event-{game_key}",
                )
            except RuntimeError:
                pass
        except Exception:
            logger.warning(
                "[LaunchHistory] _emit_state failed for %s/%s",
                game_key,
                trigger,
            )

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs) -> None:
        """React to a ``GAME_STOPPED`` event and update the history.

        Decision tree:

        * **Clean exit** (``exit_code == 0``) → record a success
          (which clears any pending failure window for the game).
        * **Killed by a signal** (e.g. user hit "Force quit") →
          neither success nor failure — the user explicitly
          terminated, not a crash.
        * **Non-zero exit + fast boot** (less than
          ``fast_boot_seconds`` elapsed) → record a fast-boot
          failure; this may flip the circuit if the threshold is
          reached.
        * **Non-zero exit + long session** → ignored ; a game
          that ran for 30 minutes then crashed isn't an "always
          fails to launch" candidate.
        """
        store = kwargs.get("store", "")
        game_id = kwargs.get("game_id", "")
        exit_code = kwargs.get("exit_code", 0)
        elapsed = kwargs.get("elapsed_seconds", 0.0)
        terminated_by_signal = kwargs.get("terminated_by_signal", False)
        if not store or not game_id:
            return
        game_key = f"{store}:{game_id}"
        if exit_code == 0:
            self.record_success(game_key)
            return
        if terminated_by_signal:
            return
        if exit_code != 0 and elapsed < self.fast_boot_seconds():
            self.record_failure(game_key, FAILURE_KIND_FAST_BOOT)
