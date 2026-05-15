"""services/launch_history/bypass.py — Force-launch bypass (filesystem IPC).

Frontend "Force launch" button → plugin RPC
``arm_circuit_bypass`` → ``arm_bypass``. Launcher CLI's
``_build_context`` → ``consume_bypass`` before each launch.
The two processes never share memory; the JSON file IS the IPC
channel. Atomic writes on both sides (tmpfile + rename)
guarantee neither process ever sees a partial state.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from .persistence import load_history, save_history

logger = logging.getLogger(__name__)

# Bypass validity window. 5 minutes is long enough for the user
# to navigate from "Force launch" to Play in the Steam Library,
# short enough that an accidental arm doesn't disable the
# breaker for the day.
_BYPASS_VALIDITY_SECONDS = 300


class _BypassMixin:
    """One-shot force-launch bypass for LaunchHistoryService."""

    # Provided by host class
    _path: Path

    def arm_bypass(self, game_key: str) -> None:
        """Mark ``game_key`` as eligible for a single bypass."""
        try:
            data = load_history(self._path)

            if game_key not in data:
                data[game_key] = {}

            now = time.time()
            data[game_key]["bypass_armed"] = now

            save_history(self._path, data)
            logger.info("[LaunchHistory] Armed bypass for %s (valid for %ds)", game_key, _BYPASS_VALIDITY_SECONDS)

            # Host provides _emit_state
            if hasattr(self, "_emit_state"):
                self._emit_state(game_key, "arm_bypass")

        except Exception as e:
            logger.warning("[LaunchHistory] Failed to arm bypass for %s: %s", game_key, e)

    def consume_bypass(self, game_key: str) -> bool:
        """Atomically check and consume an armed bypass flag."""
        try:
            data = load_history(self._path)

            game_data = data.get(game_key)
            if not game_data or "bypass_armed" not in game_data:
                return False

            armed_at = game_data.pop("bypass_armed")
            save_history(self._path, data)

            now = time.time()
            if now - armed_at <= _BYPASS_VALIDITY_SECONDS:
                logger.info("[LaunchHistory] Consumed valid bypass for %s", game_key)
                return True

            logger.debug("[LaunchHistory] Ignored expired bypass for %s", game_key)
            return False

        except Exception as e:
            logger.warning("[LaunchHistory] Failed to consume bypass for %s: %s", game_key, e)
            return False
