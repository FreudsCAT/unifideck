"""StorageRPCMixin — install-location enumeration + mutation RPCs.

OP-26j | py_modules/unifideck/rpc/mixins/storage.py

Split out of ``DownloadRPCMixin`` so the download mixin keeps
its single responsibility (queue + install lifecycle) and this
file owns everything storage-related :

* ``get_storage_locations``     — list of `(id, label, path, free_space_gb)`
* ``set_default_storage_location`` — persist user pick
* ``set_custom_install_path``   — persist a custom path

Shape matches the frontend ``StorageLocationsResponse`` contract
in ``src/types/downloads.ts``. The mixin pulls its filesystem
enumeration from ``unifideck.utils.paths.get_all_game_directories``
and classifies each path via the module-level helpers below.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from unifideck.rpc import RpcError

logger = logging.getLogger(__name__)


class StorageRPCMixin:
    """Install-location RPC : enumerate + mutate config."""

    config: Any

    async def get_storage_locations(self) -> Any:
        """Return install locations + the active default.

        See module docstring for the response shape. Reads
        ``download.custom_path`` and ``download.default_location``
        from config to classify each enumerated directory and
        report which one the user picked as the default.
        """
        from unifideck.utils.paths import get_all_game_directories

        config = getattr(self, "config", None)
        custom_path: str | None = None
        if config is not None:
            try:
                custom_path = config.get("download.custom_path", None)
            except Exception:
                custom_path = None
        locations: list[dict[str, Any]] = []
        for path in get_all_game_directories(config):
            try:
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            loc_id = _classify_storage_location(path, custom_path)
            locations.append({
                "id": loc_id,
                "label": _storage_label(loc_id, path),
                "path": path,
                "available": True,
                "free_space_gb": round(usage.free / (1024 ** 3), 1),
            })
        default = "internal"
        if config is not None:
            try:
                default = config.get(
                    "download.default_location", "internal",
                )
            except Exception as e:
                logger.debug(
                    "[storage] reading download.default_location failed: %s", e,
                )
        return {
            "success": True,
            "locations": locations,
            "default": default,
        }

    async def set_default_storage_location(self, loc_id: str) -> Any:
        """Persist the user's preferred default storage location.

        Args:
            loc_id: one of ``"internal"`` / ``"sdcard"`` /
                ``"custom"``.

        Returns:
            ``{success: True, default: <loc_id>}``.

        Raises:
            RpcError: ``invalid_location`` for unknown ids,
                ``service_unavailable`` if config isn't wired.
        """
        if loc_id not in ("internal", "sdcard", "custom"):
            raise RpcError("invalid_location", loc_id=loc_id)
        config = getattr(self, "config", None)
        if config is None:
            raise RpcError("service_unavailable", service="config")
        config.set("download.default_location", loc_id)
        return {"success": True, "default": loc_id}

    async def set_custom_install_path(self, path: str) -> Any:
        """Persist a user-picked custom install root.

        Validates that the path exists and is writable
        before saving — the download service relies on this
        path being usable, so we want to fail fast at
        save-time rather than silently break the install
        later. Filesystem checks (``realpath``, ``is_dir``,
        ``os.access``) are wrapped in ``asyncio.to_thread``
        so the event loop is never blocked on a slow mount.
        """
        config = getattr(self, "config", None)
        if config is None:
            raise RpcError("service_unavailable", service="config")
        resolved = await asyncio.to_thread(
            lambda: str(Path(path or "").expanduser().resolve()),
        )
        is_dir = await asyncio.to_thread(Path(resolved).is_dir)
        if not resolved or not is_dir:
            return {
                "success": False,
                "error": "path_not_a_directory",
                "path": resolved,
            }
        writable = await asyncio.to_thread(os.access, resolved, os.W_OK)
        if not writable:
            return {
                "success": False,
                "error": "path_not_writable",
                "path": resolved,
            }
        config.set("download.custom_path", resolved)
        return {"success": True, "path": resolved}


# ─── Module-level helpers ─────────────────────────────────────
#
# Used by `get_storage_locations` to map a filesystem path back
# to the `StorageLocation` discriminator the frontend expects
# (`internal` / `sdcard` / `custom`).

def _classify_storage_location(
    path: str,
    custom_path: str | None,
) -> str:
    """Classify a filesystem path as internal / sdcard / custom.

    Order matters : a configured ``custom_path`` always wins
    over the path-prefix heuristic so the user-picked path
    is reported as ``custom`` even if it happens to live
    under ``/run/media``.
    """
    if custom_path and path.rstrip("/") == custom_path.rstrip("/"):
        return "custom"
    if path.startswith(("/run/media/", "/mnt/")):
        return "sdcard"
    return "internal"


def _storage_label(loc_id: str, path: str) -> str:
    """Default label for a storage location.

    The frontend renders its own i18n label via
    ``storageSettings.internalStorage`` etc., but the backend
    provides a sensible default for unstyled callers (logs,
    diagnostics, tests).
    """
    if loc_id == "internal":
        return "Internal storage"
    if loc_id == "sdcard":
        return "SD card"
    return path
