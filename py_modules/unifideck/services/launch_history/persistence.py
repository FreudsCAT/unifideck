"""Launch history persistence.

OP-21e | py_modules/unifideck/services/launch_history/persistence.py

Two functions for serialising the launch history to disk :

* ``load_history`` — read the JSON file on boot;
* ``save_history`` — atomic write after every state change.

The history is stored as a per-game list of ``(timestamp, code)``
tuples, capped at the configured retention size.
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


def load_history(path: Path) -> dict[str, Any]:
    """Load the persisted launch history from disk.

    Returns an empty dict when the file is absent (first boot) or
    when the JSON is malformed (logged at WARN). A corrupt file
    is recoverable — the user just loses the failure history for
    the games involved, but their circuit breakers reset and
    everything keeps working.

    Args:
        path: absolute path of the history JSON file.

    Returns:
        Mapping ``"game_key" → {failures: [...], bypass_armed: ts}``.
    """
    if not path.exists():
        return {}
    try:
        return cast(
            "dict[str, Any]",
            json.loads(path.read_text() or "{}"),
        )
    except (json.JSONDecodeError, OSError) as err:
        logger.warning(
            "[LaunchHistory] could not read %s: %s — starting fresh",
            path,
            err,
        )
        return {}


def save_history(path: Path, data: dict[str, Any]) -> None:
    """Persist the launch history to disk atomically.

    Creates the parent directory if absent, writes to ``<path>.tmp``,
    then ``replace`` to swap the temp file over the target.
    ``replace`` is atomic on every supported filesystem.

    On failure, the temp file is cleaned up so a partial write
    can't accumulate. The save error itself is logged at ERROR
    (not WARN) because losing the launch history means the
    circuit breaker decisions will be wrong for a while.

    Args:
        path: absolute path of the history JSON file.
        data: the mapping to serialise.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(path)
    except OSError as err:
        logger.error(
            "[LaunchHistory] save failed for %s: %s",
            path,
            err,
        )
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
