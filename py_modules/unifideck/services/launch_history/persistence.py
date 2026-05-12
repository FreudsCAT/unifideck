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
    """Load history."""
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
    """Save history."""
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
