"""steam/shortcuts.py — Steam shortcuts.vdf I/O utilities.
Moved from shortcuts/vdf.py and renamed. The old
location was a solo-file subpackage whose identity was unclear
(was it "all shortcut management" or "just VDF codec"?). The
new home makes the answer explicit: this is **Steam's**
shortcuts file format, handled alongside the other Steam-client
interactions (library scan, SteamGridDB artwork). The former
filename `vdf.py` was also misleading — this module is NOT a
generic VDF codec; all 4 public functions are hardcoded to the
shortcuts.vdf layout (load_shortcuts_vdf, read_shortcuts,
save_shortcuts_vdf, write_shortcuts).
Thin wrapper around the external `vdf` library with safer
I/O semantics:
- Atomic write-then-rename so Steam never reads a half-written
  file
- Automatic `.backup` before overwriting (for rollback)
- Write validation: re-parse the file after writing and compare
  shortcut counts; restore the backup if validation fails
- Structured logging instead of `print` statements
- Functions are synchronous — callers should wrap them in
  `asyncio.to_thread()` (done by ShortcutService via AsyncFileOps)
The legacy module used `print()` for errors and didn't do atomic
writes, which created a small window where `shortcuts.vdf` could
be read by Steam mid-write and corrupt the library. This version
eliminates that race condition.
Reference: Technical Document v1.0 — Section 3.6.2 (shortcuts.vdf
format), Figure 23.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any, cast

# Module logger always lives at module scope. The previous
# version only defined it inside the `except ImportError` branch
# below, which meant `logger.info(...)` later in the module
# raised `NameError: name 'logger' is not defined` whenever the
# `vdf` import succeeded (the normal case).
logger = logging.getLogger(__name__)

# vdf is loaded conditionally so the plugin can boot when the
# library is missing (vendored under defaults/python at install
# time). The Optional annotation tells mypy that None is a valid
# fallback; call sites guard with ``if vdf is None`` before using.
try:
    import vdf
except ImportError:
    vdf = None  # type: ignore[assignment]
class VDFError(Exception):
    """Raised when VDF parsing or writing fails."""
def load_shortcuts_vdf(path: str) -> dict[str, Any]:
    """Load and parse a shortcuts.vdf file.
    Returns an empty `{"shortcuts": {}}` structure if the file does
    not exist (Steam's behavior on first plugin run). Raises
    VDFError if the file exists but cannot be parsed.
    """

    if vdf is None:
        raise VDFError("vdf library not installed")
    if not Path(path).is_file():
        return {"shortcuts": {}}
    try:
        with Path(path).open("rb") as f:
            return cast("dict[str, Any]", vdf.binary_loads(f.read()))  # type: ignore[no-untyped-call]
    except Exception as e:
        raise VDFError(f"failed to parse {path}: {e}") from e

def read_shortcuts(path: str) -> list[dict[str, Any]]:
    """Convenience: return the list of shortcut entries.
    The raw VDF structure is `{"shortcuts": {"0": {...}, "1": {...}}}`
    where keys are string-indexed. ShortcutService prefers working
    with a flat list, so we flatten here and renumber on save.
    """
    data = load_shortcuts_vdf(path)
    raw = data.get("shortcuts", {})
    if isinstance(raw, dict):
        # Preserve insertion order using the numeric string keys
        return [raw[k] for k in sorted(raw.keys(), key=_sort_key)]
    return []
def _atomic_write_tmp(tmp_path: str, data: dict[str, Any]) -> None:
    """Write VDF data to ``tmp_path`` with fsync, raising on error.

    Single-purpose helper that bundles the four-syscall write
    sequence (open / write / flush / fsync) used by
    ``save_shortcuts_vdf``'s "step 2". Extracted to keep the
    parent function's fan-out under the project's 10-callee
    policy — without it we'd have ``Path``, ``binary_dumps``,
    ``flush``, ``fsync``, ``fileno`` as five separate targets
    in the orchestrator.

    Raises VDFError on any underlying OS error so the caller
    only has to handle one exception type.
    """
    try:
        with Path(tmp_path).open("wb") as f:
            f.write(vdf.binary_dumps(data))  # type: ignore[no-untyped-call]
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        _cleanup_tmp(tmp_path)
        raise VDFError(f"write failed: {e}") from e


def save_shortcuts_vdf(path: str, data: dict[str, Any]) -> None:
    """Write the full shortcuts.vdf structure atomically.
    Performs a 3-step write: backup → write to ``.tmp`` → rename.
    This guarantees Steam never sees a partial file even if the
    process is killed mid-write.
    Also validates the write by re-reading the file and comparing
    the shortcut count; restores from backup if validation fails.
    Raises VDFError on any failure.

    Refactor history (2026-05-14): the atomic write phase was
    extracted to ``_atomic_write_tmp`` to keep this function's
    fan-out under the 10-callee policy after the PTH118 pass
    introduced ``Path`` as an additional call target.
    """
    if vdf is None:
        raise VDFError("vdf library not installed")
    expected_count = len(data.get("shortcuts", {}))
    tmp_path = f"{path}.tmp"
    backup_path = f"{path}.backup"
    # Step 1: back up the current file (if any)
    if Path(path).is_file():
        try:
            shutil.copy2(path, backup_path)
        except OSError as e:
            raise VDFError(f"backup failed: {e}") from e
    # Step 2: write to a temporary file (atomically, with fsync)
    _atomic_write_tmp(tmp_path, data)
    # Step 3: atomic rename
    try:
        Path(tmp_path).replace(path)
    except OSError as e:
        _cleanup_tmp(tmp_path)
        raise VDFError(f"rename failed: {e}") from e
    # Step 3b: keep the file executable so NonSteamLaunchers' scanner
    # does not treat it as "uninitialised" and wipe it (see the primary
    # writer, services/shortcut/persistence.write_vdf, for the full
    # rationale). A chmod failure must not fail an otherwise-good write.
    with contextlib.suppress(OSError):
        Path(path).chmod(0o755)
    # Step 4: validate the write
    try:
        validation = load_shortcuts_vdf(path)
        actual_count = len(validation.get("shortcuts", {}))
    except VDFError as e:
        _restore_backup(backup_path, path)
        raise VDFError(f"validation read failed: {e}") from e
    if actual_count != expected_count:
        _restore_backup(backup_path, path)
        raise VDFError(
            f"validation count mismatch: expected "
            f"{expected_count}, got {actual_count} — backup "
            f"restored",
        )
    logger.info(
        "[vdf] wrote %d shortcuts to %s", actual_count, path,
    )
def write_shortcuts(path: str, shortcuts: list[dict[str, Any]]) -> None:
    """Convenience: serialize a flat list of shortcut entries.
    Renumbers the entries as string keys ("0", "1", "2", ...) which
    is the format Steam expects. Delegates the actual write to
    `save_shortcuts_vdf()`.
    """
    data = {"shortcuts": {str(i): entry
    for i, entry in enumerate(shortcuts)}}
    save_shortcuts_vdf(path, data)

    # ── Helpers ─────────────────────────────────────────────────────
def _sort_key(k: str) -> int:
    """Sort key for shortcut dict keys (numeric strings)."""
    try:
        return int(k)
    except ValueError:
        return 999999 # non-numeric keys go to the end
def _cleanup_tmp(tmp_path: str) -> None:
    """Remove a leftover .tmp file, ignoring errors."""
    with contextlib.suppress(OSError):
        Path(tmp_path).unlink()
def _restore_backup(backup_path: str, path: str) -> None:
    """Restore the backup file over the target (best-effort)."""
    if Path(backup_path).is_file():
        try:
            shutil.copy2(backup_path, path)
            logger.warning("[vdf] restored backup to %s", path)
        except OSError:
            logger.exception("[vdf] backup restore failed")
