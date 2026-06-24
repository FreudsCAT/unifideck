"""services/achievements/state.py — persisted per-game achievement state.

One JSON file keyed by ``{store}:{game_id}`` → ``{unlocked_keys, last_session}``.
The watcher writes it (baseline at launch, reconcile at game-stop); the RPC
layer reads ``last_session`` for the game-info "last session" summary — a fast,
network-free read off the ``get_game_info`` hot path. Read-modify-write under a
lock + atomic replace so a concurrent reader never sees a torn file.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(
    "~/.local/share/unifideck/achievement_state.json",
).expanduser()


class AchievementStateStore:
    """Per-game ``{unlocked_keys, last_session}`` persisted to one JSON file."""

    def __init__(self, path: str | Path | None = None) -> None:
        """Initialize the instance."""
        self._path = Path(path).expanduser() if path else _DEFAULT_PATH
        self._lock = threading.Lock()

    def get(self, store: str, game_id: str) -> dict[str, Any] | None:
        """Return the persisted entry for a game, or None."""
        with self._lock:
            return self._read().get(f"{store}:{game_id}")

    def update(
        self,
        store: str,
        game_id: str,
        *,
        unlocked_keys: Iterable[str] | None = None,
        last_session: dict[str, Any] | None = None,
    ) -> None:
        """Merge fields into a game's entry (read-modify-write, atomic)."""
        key = f"{store}:{game_id}"
        with self._lock:
            data = self._read()
            entry = dict(data.get(key) or {})
            if unlocked_keys is not None:
                entry["unlocked_keys"] = sorted(set(unlocked_keys))
            if last_session is not None:
                entry["last_session"] = last_session
            data[key] = entry
            self._write(data)

    # -- internals ---------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        """Read."""
        try:
            if self._path.is_file():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            logger.debug("[achievements.state] read failed", exc_info=True)
        return {}

    def _write(self, data: dict[str, Any]) -> None:
        """Write."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            logger.warning("[achievements.state] write failed", exc_info=True)
