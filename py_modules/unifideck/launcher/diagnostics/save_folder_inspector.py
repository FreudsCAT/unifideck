from __future__ import annotations
import logging
import os
from typing import Any
logger = logging.getLogger(__name__)
def inspect_save_folder(
    root: str,
    *,
    max_depth: int = 2,
    filter_substring: str = "",
    max_files: int = 500,
) -> dict[str, Any]:
    """Inspect save folder."""
    result: dict[str, Any] = {
        "path": root,
        "exists": False,
        "total_files": 0,
        "total_size": 0,
        "files": [],
        "truncated_count": 0,
        "truncated_size": 0,
    }
    if not os.path.isdir(root):
        return result
    result["exists"] = True
    substr = filter_substring.lower() if filter_substring else ""
    all_entries = _collect_file_entries(root, max_depth, substr)
    result["total_files"] = len(all_entries)
    result["total_size"] = sum(e["size"] for e in all_entries)
    all_entries.sort(key=lambda e: e["size"], reverse=True)
    _apply_file_cap(all_entries, max_files, result)
    return result

def _collect_file_entries(
    root: str, max_depth: int, substr: str,
) -> list[dict[str, Any]]:

    """Collect file entries."""
    entries: list[dict[str, Any]] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
            if max_depth >= 0 and depth >= max_depth:
                dirnames[:] = []
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel_norm = os.path.relpath(full, root).replace(
                    os.sep, "/",
                )
                if substr and substr not in rel_norm.lower():
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                entries.append({
                    "rel_path": rel_norm,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })
    except OSError as err:
        logger.warning(
            "[save_folder_inspector] walk failed for %s: %s",
            root, err,
        )
    return entries
def _apply_file_cap(
    entries: list[dict[str, Any]],
    max_files: int,
    result: dict[str, Any],
) -> None:
    """Apply file cap."""
    if len(entries) > max_files:
        result["files"] = entries[:max_files]
        dropped = entries[max_files:]
        result["truncated_count"] = len(dropped)
        result["truncated_size"] = sum(e["size"] for e in dropped)
    else:
        result["files"] = entries