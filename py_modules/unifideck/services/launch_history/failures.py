"""services/launch_history/failures.py — Failures read/write + circuit predicate.

Mixin exposing the failures API (get / record / clear / success)
and the ``is_circuit_open`` predicate. Host must provide
``_path``, ``window_seconds()``, ``threshold()``, ``_emit_state``.
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
    """Failures API + circuit predicate for LaunchHistoryService."""

    # Provided by host class.
    _path: Path

    def get_recent_failures(self, game_key: str) -> list[dict[str, Any]]:
        """Return failures for a game within the sliding window."""
        try:
            data = load_history(self._path)
            game_data = data.get(game_key, {})
            failures = game_data.get("failures", [])

            if not failures:
                return []

            # Filter in memory
            now = time.time()
            window = self.window_seconds()

            return [f for f in failures if now - f.get("timestamp", 0) <= window]

        except Exception as e:
            logger.debug("[LaunchHistory] get_recent_failures failed for %s: %s", game_key, e)
            return []

    def is_circuit_open(self, game_key: str) -> tuple[bool, int]:
        """True if the game has hit the failure threshold."""
        recent = self.get_recent_failures(game_key)
        count = len(recent)

        return count >= self.threshold(), count

    def record_failure(self, game_key: str, kind: str, error_code: str = "") -> None:
        """Append a failure entry for a game."""
        if kind not in _VALID_KINDS:
            logger.warning("[LaunchHistory] Invalid failure kind %r for %s, dropping", kind, game_key)
            return

        try:
            data = load_history(self._path)

            # Opportunistic GC: prune expired entries for all games
            now = time.time()
            window = self.window_seconds()

            for k, v in list(data.items()):
                if "failures" in v:
                    v["failures"] = [f for f in v["failures"] if now - f.get("timestamp", 0) <= window]
                    if not v["failures"] and "bypass_armed" not in v:
                        del data[k]

            # Append new failure
            if game_key not in data:
                data[game_key] = {}
            if "failures" not in data[game_key]:
                data[game_key]["failures"] = []

            data[game_key]["failures"].append({
                "timestamp": now,
                "kind": kind,
                "error_code": error_code,
            })

            save_history(self._path, data)
            logger.info("[LaunchHistory] Recorded %s failure for %s", kind, game_key)

            if hasattr(self, "_emit_state"):
                self._emit_state(game_key, f"record_failure_{kind}")

        except Exception as e:
            logger.warning("[LaunchHistory] Failed to record failure for %s: %s", game_key, e)

    def clear_failures(self, game_key: str) -> None:
        """Remove all failures for a game + emit state change."""
        try:
            data = load_history(self._path)

            if game_key in data and "failures" in data[game_key]:
                del data[game_key]["failures"]
                if not data[game_key]:
                    del data[game_key]

                save_history(self._path, data)
                logger.info("[LaunchHistory] Cleared failures for %s", game_key)

            if hasattr(self, "_emit_state"):
                self._emit_state(game_key, "clear_failures")

        except Exception as e:
            logger.warning("[LaunchHistory] Failed to clear failures for %s: %s", game_key, e)

    def record_success(self, game_key: str) -> None:
        """Wipe failure history after a successful launch."""
        try:
            data = load_history(self._path)

            if game_key in data and "failures" in data[game_key]:
                del data[game_key]["failures"]
                if not data[game_key]:
                    del data[game_key]

                save_history(self._path, data)
                logger.info("[LaunchHistory] Wiped failures after success for %s", game_key)

            if hasattr(self, "_emit_state"):
                self._emit_state(game_key, "closed")

        except Exception as e:
            logger.warning("[LaunchHistory] Failed to record success for %s: %s", game_key, e)
