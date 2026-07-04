"""config/config_persistence.py — File I/O for ConfigManager.

Moved from core/ to unifideck.config. A shim at
core/config_persistence.py re-exports the public symbols for
backward compatibility.


  - `load_json_layer(path)` — tolerant JSON read that returns an
    empty dict on any failure, with a warning. Drops meta keys
    starting with underscore (comments/schema hints). Used for
    both defaults and user overrides.

  - `atomic_write_json(path, data, mode)` — write-to-temp-then-
    rename pattern so a crash mid-write never leaves a truncated
    config.json on disk. Mode defaults to 0o600 (P2.1 fix C6:
    user configs may contain sensitive paths).

All filesystem operations go through `pathlib.Path` for clarity.
Callers must handle `ConfigSchemaError` if they want to react
to validation failures — persistence itself is schema-agnostic.

Reference: Technical Document v1.0 — Section 3.4.4 (ConfigManager).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_json_layer(path: Path) -> dict[str, Any]:
    """Read a single JSON config layer from disk.

    Returns an empty dict on any failure (file missing, permission
    error, malformed JSON) and logs a warning. This tolerant
    behaviour is deliberate: the caller will merge multiple layers
    and can proceed with whatever it successfully read.

    Meta keys starting with `_` are dropped — they're used by
    config authors to leave JSON-safe comments or schema hints
    that should never reach runtime consumers.
    """
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            "[config_persistence] %s unreadable (%s): %s",
            path, type(e).__name__, e,
        )
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "[config_persistence] %s is not a JSON object (got %s) — "
            "ignoring",
            path, type(data).__name__,
        )
        return {}

    # Drop meta keys (comments, schema hints, etc.)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _write_payload_and_publish(
    fd: int, tmp_path: str, target: Path, data: dict[str, Any], mode: int,
) -> None:
    """Write JSON to the temp fd, chmod it, then atomic-rename to ``target``.

    Single-purpose helper extracted from ``atomic_write_json`` to
    keep that function's fan-out under the 10-callee policy. Groups
    the post-mkstemp sequence (fdopen / dump / flush / fsync /
    chmod / replace) into one orchestrated step.

    The caller still owns the failure-cleanup (unlinking the
    leftover tmp file) since that logic is intertwined with the
    bookkeeping ``tmp_path`` var that signals "rename succeeded".
    """
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    # Apply the requested mode BEFORE the rename so the
    # published file never exists with default (0o644+) mode.
    Path(tmp_path).chmod(mode)
    # Atomic swap. If this raises, the caller's old config is
    # still intact on disk, unmodified.
    Path(tmp_path).replace(target)


def atomic_write_json(
    path: Path,
    data: dict[str, Any],
    mode: int = 0o600,
) -> None:
    """Write JSON to `path` atomically.

    Uses the write-to-temp-then-rename pattern so a crash mid-write
    cannot leave a truncated file on disk. The temp file is created
    in the same directory as `path` to guarantee the final rename
    stays on the same filesystem (cross-fs renames are not atomic).

    The default mode is 0o600 because user configs may contain
    sensitive paths (data dirs, Steam install paths, OAuth-related
    filenames). Callers can override for files that genuinely need
    to be world-readable.

    Raises:
      OSError: if the parent directory cannot be created, or the
        temp file cannot be renamed. Callers should catch and log.

    Refactor history (2026-05-15): extracted
    ``_write_payload_and_publish`` to bring this function's fan-out
    under the 10-callee policy after the SIM105 pass introduced
    ``contextlib.suppress`` as a new call target.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create the temp file in the same directory as the target to
    # guarantee the rename is atomic (single filesystem).
    fd, tmp_path_init = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path: str | None = tmp_path_init
    try:
        _write_payload_and_publish(fd, tmp_path_init, path, data, mode)
        tmp_path = None  # successfully renamed, nothing to clean
    finally:
        # Defence in depth: if anything above raised before the
        # rename, the temp file still exists and must be removed.
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink()
