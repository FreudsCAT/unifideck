"""Launch history — failure recording + querying.

OP-21b | py_modules/unifideck/services/launch_history/failures.py

``_FailuresMixin`` exposes the methods to :

* ``record_failure(game, code)`` — append a failure entry;
* ``record_success(game)`` — clear the failure streak for a game;
* ``recent_failures_for(game)`` — list within the rolling window;
* ``is_failing(game)`` — boolean decision used by the circuit breaker.

Failures are stored as tuples ``(timestamp, game_id, error_code)``
in a circular buffer per game.
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
    """Failures mixin."""

    _path: Path

    def get_recent_failures(self, game_key: str) -> list[dict[str, Any]]:
        """Get recent failures."""
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
        """Check whether circuit open."""
        threshold: int = self.threshold()
        return len(self.get_recent_failures(game_key)) >= threshold

    def record_failure(self, game_key: str, kind: str) -> None:
        """Record failure."""
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
        """Clear failures."""
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
        """Record success."""
        if load_history(self._path).get(game_key):
            self.clear_failures(game_key)
        else:
            self._emit_state(game_key, "record_success")
