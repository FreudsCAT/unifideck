"""services/launch_history/persistence.py — Atomic JSON load/save.

Same pattern as games.map: write to sibling ``.tmp``, flush,
``os.replace`` into final position. POSIX guarantees readers
see either old or new content, never partial. Free functions
(no ``self``) — path passed explicitly so tests isolate state.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_history(path: Path) -> dict[str, Any]:
    """Read + parse the JSON file. Returns ``{}`` on any error."""
    if not path.is_file():
        return {}

    try:
        with Path(path).open(encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            logger.warning("[LaunchHistory] %s is not a dictionary", path)
            return {}

        return data
    except json.JSONDecodeError as e:
        logger.warning("[LaunchHistory] Malformed JSON in %s: %s", path, e)
        return {}
    except Exception as e:
        logger.warning("[LaunchHistory] Failed to load %s: %s", path, e)
        return {}


def save_history(path: Path, data: dict[str, Any]) -> None:
    """Write the JSON file atomically (tmp + replace)."""
    tmp_path = path.with_suffix(".json.tmp")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        with Path(tmp_path).open("w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())

        Path(tmp_path).replace(path)
    except Exception:
        logger.exception("[LaunchHistory] Failed to save history to %s", path)
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
