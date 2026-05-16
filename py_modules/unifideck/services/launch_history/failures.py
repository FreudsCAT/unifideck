"""services/launch_history/failures.py — Failures read/write + circuit predicate.

Mixin exposing the failures API (get / record / clear / success)
and the ``is_circuit_open`` predicate. Host must provide
``_path``, ``window_seconds()``, ``threshold()``, ``_emit_state``.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .constants import _VALID_KINDS
from .persistence import load_history, save_history

logger = logging.getLogger(__name__)


def _gc_expired_entries(
    data: dict[str, Any], now: float, window: float,
) -> None:
    """Drop failure entries older than ``window`` seconds, in place.

    Opportunistic garbage-collect — runs on every ``record_failure``
    so the on-disk history doesn't grow unbounded. A game whose
    last failure rolls out of the window AND that has no
    ``bypass_armed`` flag is removed entirely (its dict is empty
    after the prune).
    """
    for k, v in list(data.items()):
        if "failures" not in v:
            continue
        v["failures"] = [
            f for f in v["failures"]
            if now - f.get("timestamp", 0) <= window
        ]
        if not v["failures"] and "bypass_armed" not in v:
            del data[k]


def _append_failure_entry(
    data: dict[str, Any],
    game_key: str,
    kind: str,
    error_code: str,
    now: float,
) -> None:
    """Append a freshly-built failure record to ``data[game_key]``.

    Defensively initialises the nested dicts so callers don't
    have to know the persistence layer's schema. The added entry
    has the same shape regardless of whether the game was
    previously known.
    """
    if game_key not in data:
        data[game_key] = {}
    if "failures" not in data[game_key]:
        data[game_key]["failures"] = []
    data[game_key]["failures"].append({
        "timestamp": now,
        "kind": kind,
        "error_code": error_code,
    })


class _FailuresMixin:
    """Failures API + circuit predicate for LaunchHistoryService.

    Expects the composing host to provide:

    * ``_path: Path``                  — on-disk history file
    * ``window_seconds() -> float``    — sliding window length
    * ``threshold() -> int``           — failure count that
                                         opens the circuit
    * ``_emit_state(...)``             — bus emit helper

    The first attribute is annotated here so mypy can see it
    when the mixin is type-checked in isolation. The method
    callables (``window_seconds``, ``threshold``) are declared
    as TYPE_CHECKING-only stubs because they're provided by
    the host's instance methods at runtime through the MRO.
    """

    # Provided by host class.
    _path: Path

    if TYPE_CHECKING:
        # Type-only declarations of host-provided methods so
        # mypy can resolve ``self.window_seconds()`` and
        # ``self.threshold()`` calls below.
        def window_seconds(self) -> float: ...
        def threshold(self) -> int: ...
        def _emit_state(
            self, *args: Any, **kwargs: Any,
        ) -> None: ...

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
        """Append a failure entry for a game.

        Refactor history (2026-05-14): inlined GC sweep (double
        for + 2 ifs) + append flow with the try/except envelope
        was at CC=12. Pulled the GC and append into helpers so
        the public method is a flat read.
        """
        if kind not in _VALID_KINDS:
            logger.warning(
                "[LaunchHistory] Invalid failure kind %r for %s, dropping",
                kind, game_key,
            )
            return

        try:
            data = load_history(self._path)
            now = time.time()
            _gc_expired_entries(data, now, self.window_seconds())
            _append_failure_entry(data, game_key, kind, error_code, now)
            save_history(self._path, data)
            logger.info(
                "[LaunchHistory] Recorded %s failure for %s", kind, game_key,
            )
            if hasattr(self, "_emit_state"):
                self._emit_state(game_key, f"record_failure_{kind}")
        except Exception as err:
            logger.warning(
                "[LaunchHistory] Failed to record failure for %s: %s",
                game_key, err,
            )

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
