"""launcher/diagnostics/save_folder_inspector.py — Save folder inspection.

Reads a game's save-data root, enumerates files up to a given
depth, optionally filters by substring, sorts by size, and caps
the result at ``max_files`` (returning truncation totals so the
UI can show "+N more files (M MiB)" without listing them all).

Pure-IO module: no service deps, no async. The caller (typically
``SaveFolderInspectorService``) runs ``inspect_save_folder`` in
a worker thread when invoked from an async context.

Refactor history (2026-05-14): ``_collect_file_entries`` was a
single function at CC=16 — a nested ``for-dir / for-file`` walk
with depth gating, substring filtering and per-file ``os.stat``
guarded by its own ``try/except``. Split into three helpers so
the main loop stays a flat read; cognitive complexity dropped
to single digits with zero change to the public contract.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def inspect_save_folder(
    root: str,
    *,
    max_depth: int = 2,
    filter_substring: str = "",
    max_files: int = 500,
) -> dict[str, Any]:
    """Inspect ``root`` and return a save-folder report.

    The returned dict always has the same shape — even on a
    non-existent root — so frontend code can rely on the keys
    without optional-chaining every access.

    Args:
        root: Absolute path to the save folder to inspect.
        max_depth: How many subdirectory levels to descend.
            ``0`` means "files directly in ``root`` only";
            ``-1`` means unlimited.
        filter_substring: Case-insensitive substring filter
            applied on the POSIX-normalised relative path
            (``a/b/c.sav``). Empty string disables filtering.
        max_files: Cap the returned ``files`` list to its first
            N entries (after sort-by-size desc). Truncated tail
            still contributes to ``truncated_count`` and
            ``truncated_size`` for the UI summary.

    Returns:
        Dict with ``path``, ``exists``, ``total_files``,
        ``total_size``, ``files`` (list of entries),
        ``truncated_count`` and ``truncated_size``. Each entry
        in ``files`` has ``rel_path``, ``size`` and ``mtime``.
    """
    result: dict[str, Any] = {
        "path": root,
        "exists": False,
        "total_files": 0,
        "total_size": 0,
        "files": [],
        "truncated_count": 0,
        "truncated_size": 0,
    }
    if not Path(root).is_dir():
        return result

    result["exists"] = True
    substr = filter_substring.lower() if filter_substring else ""
    all_entries = _collect_file_entries(root, max_depth, substr)
    result["total_files"] = len(all_entries)
    result["total_size"] = sum(e["size"] for e in all_entries)
    all_entries.sort(key=lambda e: e["size"], reverse=True)
    _apply_file_cap(all_entries, max_files, result)
    return result


# ─────────────────────────────────────────────────────────────────
# Private helpers — extracted from a former single CC=16 function
# ─────────────────────────────────────────────────────────────────


def _collect_file_entries(
    root: str, max_depth: int, substr: str,
) -> list[dict[str, Any]]:
    """Walk ``root`` up to ``max_depth`` and gather file entries.

    Outer try wraps the entire walk so a missing root or a
    permission error mid-traversal yields a partial list rather
    than a crash. Per-directory and per-file work is delegated to
    helpers so this function stays readable as a top-level flow.
    """
    entries: list[dict[str, Any]] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # Depth gating: if we've reached the limit, clear
            # ``dirnames`` in-place so ``os.walk`` stops
            # descending into them. (Documented contract of
            # ``os.walk`` — see CPython tutorial.)
            depth = _compute_depth(dirpath, root)
            if max_depth >= 0 and depth >= max_depth:
                dirnames[:] = []
            entries.extend(
                _entries_in_dir(dirpath, filenames, root, substr),
            )
    except OSError as err:
        logger.warning(
            "[save_folder_inspector] walk failed for %s: %s",
            root, err,
        )
    return entries


def _entries_in_dir(
    dirpath: str,
    filenames: list[str],
    root: str,
    substr: str,
) -> list[dict[str, Any]]:
    """Convert one ``os.walk`` tuple's filenames into entries.

    Applies the substring filter and the per-file stat in a tight
    loop. Pulling this out of ``_collect_file_entries`` is what
    drops the outer function's nesting and cognitive load — the
    nested-for + nested-if + nested-try used to count three times
    against the parent's complexity score.
    """
    out: list[dict[str, Any]] = []
    for name in filenames:
        full = str(Path(dirpath) / name)
        rel_norm = os.path.relpath(full, root).replace(os.sep, "/")
        if substr and substr not in rel_norm.lower():
            continue
        entry = _stat_entry(full, rel_norm)
        if entry is not None:
            out.append(entry)
    return out


def _stat_entry(full: str, rel_norm: str) -> dict[str, Any] | None:
    """``os.stat`` one file and return the entry dict, or None.

    A file can vanish between the ``os.walk`` listing and our
    ``stat`` call (race with the game cleaning its own save
    folder), or be unreadable (permission denied). Either way
    we just drop it from the report — the caller already has a
    partial-result contract via the outer try in
    ``_collect_file_entries``.
    """
    try:
        st = Path(full).stat()
    except OSError:
        return None
    return {
        "rel_path": rel_norm,
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def _compute_depth(dirpath: str, root: str) -> int:
    """Compute the depth of ``dirpath`` relative to ``root``.

    Depth 0 = ``root`` itself, depth 1 = a direct subdir, etc.
    ``os.path.relpath`` returns ``"."`` for the root, which we
    map to 0 explicitly — counting ``os.sep`` on ``"."`` would
    return 0 anyway, but the ternary expresses intent.
    """
    rel_dir = os.path.relpath(dirpath, root)
    return 0 if rel_dir == "." else rel_dir.count(os.sep) + 1


def _apply_file_cap(
    entries: list[dict[str, Any]],
    max_files: int,
    result: dict[str, Any],
) -> None:
    """Slice ``entries`` at ``max_files`` and record the tail size.

    Mutates ``result`` in place — fields ``files``,
    ``truncated_count`` and ``truncated_size`` are filled here.
    The tail (entries above the cap) contributes its file count
    and byte sum so the UI can render "showing top N of M files
    (cumulative size X MiB hidden)".
    """
    if len(entries) > max_files:
        result["files"] = entries[:max_files]
        dropped = entries[max_files:]
        result["truncated_count"] = len(dropped)
        result["truncated_size"] = sum(e["size"] for e in dropped)
    else:
        result["files"] = entries
