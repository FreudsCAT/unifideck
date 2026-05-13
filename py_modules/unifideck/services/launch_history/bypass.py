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
    """One-shot circuit-breaker bypass tokens with TTL."""

    _path: Path

    def arm_bypass(self, game_key: str) -> None:
        """Record a "try anyway" request for the given game.

        Called from the RPC layer when the user clicks the
        "try anyway" button on the circuit-open toast. The bypass
        is one-shot: a single subsequent ``consume_bypass`` call
        will return ``True``, after which the bypass is gone.

        A timestamp is stored alongside the bypass so an old
        request (e.g. user clicked "try anyway" then changed their
        mind and went to sleep) doesn't silently disable the
        circuit breaker forever — the 5-minute TTL kicks in.

        Failures during the file write are caught and logged but
        not propagated — the bypass might not arm, but that's
        recoverable (the user can re-click).

        Args:
            game_key: ``"<store>:<game_id>"`` key.
        """
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
        """Atomically check + consume a pending bypass.

        Called by the launcher service when about to launch a
        game with an open circuit. Three cases:

        * **No bypass armed** → returns ``False``, launcher
          refuses the launch.
        * **Bypass armed within TTL** → returns ``True``,
          launcher proceeds; the bypass is deleted (one-shot).
        * **Bypass armed but expired** (> 5 minutes old) →
          returns ``False`` and deletes the stale token to keep
          state clean.

        The check + delete is non-atomic in the strict sense
        (two file operations), but only one launcher invocation
        happens at a time for a given game so a race is impossible
        in practice.

        Args:
            game_key: ``"<store>:<game_id>"`` key.

        Returns:
            ``True`` iff a valid, non-expired bypass was consumed.
        """
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
