"""Launch history — circuit-breaker bypass requests.

OP-21c | py_modules/unifideck/services/launch_history/bypass.py

When the circuit breaker blocks a launch, the user can request to
"try anyway". ``_BypassMixin`` tracks active bypass requests and
exposes :

* ``request_bypass(game)`` — record a bypass-allow for one launch;
* ``consume_bypass(game)`` — atomically check + consume the bypass
  (returns True iff a bypass was active, then deletes it).

Bypass tokens have a short TTL (5 minutes) so a forgotten request
doesn't permanently disable the breaker for a game.
"""

from __future__ import annotations
import logging
import time
from pathlib import Path
from .persistence import load_history, save_history

logger = logging.getLogger(__name__)
_BYPASS_VALIDITY_SECONDS = 300


class _BypassMixin:
    """Bypass mixin."""

    _path: Path

    def arm_bypass(self, game_key: str) -> None:
        """Arm bypass."""
        try:
            data = load_history(self._path)
            entry = data.setdefault(game_key, {"failures": []})
            entry["bypass_armed"] = time.time()
            save_history(self._path, data)
            logger.info(
                "[LaunchHistory] bypass armed for %s (5-minute window)",
                game_key,
            )
            self._emit_state(game_key, "arm_bypass")
        except Exception:
            logger.exception(
                "[LaunchHistory] arm_bypass failed for %s",
                game_key,
            )

    def consume_bypass(self, game_key: str) -> bool:
        """Consume bypass."""
        try:
            data = load_history(self._path)
            entry = data.get(game_key)
            if not entry or "bypass_armed" not in entry:
                return False
            armed_at = entry["bypass_armed"]
            elapsed = time.time() - armed_at
            del entry["bypass_armed"]
            save_history(self._path, data)
            if elapsed > _BYPASS_VALIDITY_SECONDS:
                logger.info(
                    "[LaunchHistory] bypass for %s expired (%.0fs old), ignored",
                    game_key,
                    elapsed,
                )
                return False
            logger.info(
                "[LaunchHistory] bypass consumed for %s (%.0fs old)",
                game_key,
                elapsed,
            )
            self._emit_state(game_key, "consume_bypass")
            return True
        except Exception:
            logger.exception(
                "[LaunchHistory] consume_bypass failed for %s",
                game_key,
            )
            return False
