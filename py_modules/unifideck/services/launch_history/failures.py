"""Failure recording mixin — append-only log with rolling-window queries.

OP-21b | py_modules/unifideck/services/launch_history/failures.py

``_FailuresMixin`` is one half of ``LaunchHistoryService`` (the
other being ``_BypassMixin``). It owns the failure log + the
circuit-breaker decision.

The on-disk format is a JSON dict
``{ "<store>:<game_id>": { "failures": [ {ts, kind}, ... ] } }``.
The mixin garbage-collects entries falling outside the rolling
window on every write so the file doesn't grow unbounded.

The host class is expected to expose ``threshold()`` and
``window_seconds()`` methods plus ``_emit_state`` for bus events.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .constants import _VALID_KINDS
from .persistence import load_history, save_history

logger = logging.getLogger(__name__)


class _FailuresMixin:
    """Per-game failure log + circuit-breaker query."""

    _path: Path

    def get_recent_failures(self, game_key: str) -> list[dict[str, Any]]:
        """Return failures for ``game_key`` within the rolling window.

        Reads from disk fresh on every call — no in-memory cache.
        The persistence layer is fast enough (a few KB of JSON)
        that caching would add complexity without measurable
        benefit.

        Args:
            game_key: ``"<store>:<game_id>"`` key.

        Returns:
            List of failure dicts ``{ts, kind}`` ordered as
            stored (chronological insertion order). Empty list if
            the game has no recent failures.
        """
        all_failures = (
            load_history(self._path)
            .get(game_key, {})
            .get(
                "failures",
                [],
            )
        )
        cutoff = time.time() - self.window_seconds()
        return [f for f in all_failures if f.get("ts", 0) >= cutoff]

    def is_circuit_open(self, game_key: str) -> bool:
        """Return whether the launch circuit is currently open.

        Open = the game has accumulated at least ``threshold()``
        failures within the rolling window. Used by the launcher
        service to decide whether to launch or to display a
        circuit-open toast.

        Args:
            game_key: ``"<store>:<game_id>"`` key.

        Returns:
            ``True`` if the circuit is open (refuse to launch).
        """
        threshold: int = self.threshold()
        return len(self.get_recent_failures(game_key)) >= threshold

    def record_failure(self, game_key: str, kind: str) -> None:
        """Append a new failure entry for ``game_key``.

        Performs three steps atomically (via the load/save pair):

        1. Validate the failure ``kind`` against the allow-list —
           unknown kinds are logged at WARN and dropped.
        2. Garbage-collect every entry older than the rolling
           window across all games (not just ``game_key``) — keeps
           the file size bounded over time without needing a
           separate cleanup task.
        3. Append the new entry for ``game_key`` and persist.

        Does not emit ``CIRCUIT_STATE_CHANGED`` directly — the
        host class's ``_emit_state`` is responsible for that, and
        it's not always called from here (the launcher service
        chains the emission after checking ``is_circuit_open``).

        Args:
            game_key: ``"<store>:<game_id>"`` key.
            kind: failure category (must be in ``_VALID_KINDS``).
        """
        if kind not in _VALID_KINDS:
            logger.warning(
                "[LaunchHistory] ignoring unknown failure kind: %s",
                kind,
            )
            return
        data = load_history(self._path)
        cutoff = time.time() - self.window_seconds()
        for gk in list(data.keys()):
            entry = data.get(gk, {})
            failures = entry.get("failures", [])
            kept = [f for f in failures if f.get("ts", 0) >= cutoff]
            if kept:
                data[gk] = {"failures": kept}
            else:
                del data[gk]
        data.setdefault(game_key, {"failures": []})
        data[game_key]["failures"].append(
            {
                "ts": time.time(),
                "kind": kind,
            }
        )
        save_history(self._path, data)
        logger.info(
            "[LaunchHistory] recorded %s for %s (total in window: %d)",
            kind,
            game_key,
            len(data[game_key]["failures"]),
        )

    def clear_failures(self, game_key: str) -> None:
        """Remove every failure entry for ``game_key``.

        Closes the circuit immediately. Used by ``record_success``
        on a clean exit (the streak is broken) and by the RPC layer
        when the user manually resets the circuit from the QAM.

        Args:
            game_key: ``"<store>:<game_id>"`` key.
        """
        data = load_history(self._path)
        if game_key in data:
            del data[game_key]
            save_history(self._path, data)
            logger.info(
                "[LaunchHistory] cleared failures for %s",
                game_key,
            )
            self._emit_state(game_key, "clear_failures")

    def record_success(self, game_key: str) -> None:
        """Record a clean launch outcome.

        If the game had any prior failures, this clears the entire
        history (a successful launch resets the streak — the
        circuit breaker is forgiving in that sense). Otherwise
        just emits a state event for diagnostics.

        Args:
            game_key: ``"<store>:<game_id>"`` key.
        """
        if load_history(self._path).get(game_key):
            self.clear_failures(game_key)
        else:
            self._emit_state(game_key, "record_success")
