"""
StorageRPCMixin — install-location enumeration + mutation RPCs.

OP-26j | py_modules/unifideck/rpc/mixins/storage.py

* ``get_storage_locations``     — list of `(id, label, path, free_space_gb)`
* ``get_browseable_devices``    — device mount-points for the file picker
* ``set_default_storage_location`` — persist user pick
* ``set_custom_install_path``   — persist a custom path

No path is hardcoded: device roots come from ``/proc/mounts``
(real mount points), storage classification uses ``st_dev``
comparison instead of string-prefix heuristics, and ``$HOME``
is resolved at runtime via ``Path.home()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from unifideck.rpc import RpcError

logger = logging.getLogger(__name__)

# Filesystem types we skip when scanning /proc/mounts.
_SKIP_FSTYPES = frozenset({
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup",
    "cgroup2", "pstore", "bpf", "debugfs", "tracefs", "hugetlbfs",
    "ramfs", "overlay", "squashfs", "fuse.gvfsd-fuse",
    "fuse.portal", "securityfs", "configfs", "efivarfs", "mqueue",
})

_VIRTUAL_PREFIXES = ("/dev/", "/sys/", "/proc/", "/run/user/")


class StorageRPCMixin:
    """Install-location RPC : enumerate + mutate config."""

    config: Any

    async def get_storage_locations(self) -> Any:
        """Return install locations — one entry per physical device.

        Device-level enumeration: reads ``/proc/mounts`` to
        discover unique writable devices (by ``st_dev``), creates
        a ``Games/`` subdirectory on each, and returns one entry
        per device plus an optional custom-path override.

        No per-store subdirectory iteration — the frontend
        ``PickStorageModal`` shows exactly one row per device.
        """
        home = str(Path.home())
        home_dev = _device_id(home)
        config = getattr(self, "config", None)

        custom_path: str | None = None
        if config is not None:
            try:
                custom_path = config.get("download.custom_path", None)
            except Exception:
                custom_path = None

        locations: list[dict[str, Any]] = []
        seen_devices: set[int] = set()

        # ── Internal storage ──────────────────────────────
        games_root = str(Path("~/Games").expanduser())
        _ensure_dir(games_root)
        locations.append({
            "id": "internal",
            "label": "Internal storage",
            "path": games_root,
            "available": True,
            "free_space_gb": _free_gb(games_root),
        })
        seen_devices.add(home_dev)

        # ── External storage (/proc/mounts) ───────────────
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    mp = parts[1]
                    fstype = parts[2]
                    if fstype in _SKIP_FSTYPES:
                        logger.debug("[storage] skipping mount %s (fstype=%s)", mp, fstype)
                        continue
                    if mp.startswith(_VIRTUAL_PREFIXES):
                        logger.debug("[storage] skipping mount %s (virtual prefix)", mp)
                        continue
                    if not os.path.isdir(mp):
                        continue
                    dev = _device_id(mp)
                    if dev == 0 or dev == home_dev or dev in seen_devices:
                        continue
                    if not _is_writable(mp):
                        continue
                    logger.info("[storage] external mount candidate: %s (fstype=%s dev=%s)", mp, fstype, dev)
                    # Use <mount>/Games, creating if needed.
                    games_path = os.path.join(mp, "Games")
                    if not os.path.isdir(games_path):
                        try:
                            os.makedirs(games_path, exist_ok=True)
                        except OSError:
                            games_path = mp  # fall back to mount root
                    name = os.path.basename(mp) or mp
                    locations.append({
                        "id": "sdcard",
                        "label": name,
                        "path": games_path,
                        "available": True,
                        "free_space_gb": _free_gb(mp),
                    })
                    seen_devices.add(dev)
        except OSError as e:
            logger.debug("[storage] /proc/mounts read failed: %s", e)

        # ── Custom path override ───────────────────────────
        if custom_path:
            locations.append({
                "id": "custom",
                "label": custom_path,
                "path": custom_path,
                "available": True,
                "free_space_gb": _free_gb(custom_path),
            })

        default = "internal"
        if config is not None:
            try:
                default = config.get("download.default_location", "internal")
            except Exception as e:
                logger.debug("[storage] reading default_location failed: %s", e)

        return {"locations": locations, "default": default}

    async def get_browseable_devices(self) -> Any:
        """Return mount-points of every writable storage device.

        Reads ``/proc/mounts`` to discover real mount points with
        no hardcoded paths.  Internal = ``$HOME``; external = every
        other writable, non-system mount that sits on a different
        device.
        """
        home = str(Path.home())
        home_dev = _device_id(home)

        devices: list[dict[str, Any]] = [
            {
                "id": "internal",
                "label": "Internal Storage",
                "path": home,
                "free_space_gb": _free_gb(home),
            },
        ]

        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    mp = parts[1]
                    fstype = parts[2]
                    if fstype in _SKIP_FSTYPES:
                        continue
                    if mp.startswith(_VIRTUAL_PREFIXES):
                        continue
                    if not os.path.isdir(mp):
                        continue
                    if _device_id(mp) == home_dev:
                        continue  # same physical device as $HOME
                    if not _is_writable(mp):
                        continue
                    name = os.path.basename(mp) or mp
                    devices.append({
                        "id": _mount_id(mp),
                        "label": name,
                        "path": mp,
                        "free_space_gb": _free_gb(mp),
                    })
        except OSError as e:
            logger.debug("[storage] /proc/mounts read failed: %s", e)

        return {"devices": devices}

    async def set_default_storage_location(self, loc_id: str) -> Any:
        """Persist the user's preferred default storage location."""
        if loc_id not in ("internal", "sdcard", "custom"):
            raise RpcError("invalid_location", loc_id=loc_id)
        config = getattr(self, "config", None)
        if config is None:
            raise RpcError("service_unavailable", service="config")
        config.set("download.default_location", loc_id)
        return {"success": True, "default": loc_id}

    async def set_custom_install_path(self, path: str) -> Any:
        """Persist a user-picked custom install root.

        Validates that the path exists and is writable before
        saving so the download service can rely on it.
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


def _free_gb(path: str) -> float:
    """Free space in GB for the filesystem containing *path*."""
    try:
        st = os.statvfs(path)
        return round((st.f_frsize * st.f_bavail) / (1024 ** 3), 1)
    except OSError:
        return 0.0


def _ensure_dir(path: str) -> None:
    """Create a directory if it doesn't exist. Idempotent."""
    Path(path).mkdir(parents=True, exist_ok=True)


def _device_id(path: str) -> int:
    """Return the ``st_dev`` of *path*, or 0 on error."""
    try:
        return os.stat(path).st_dev
    except OSError:
        return 0


def _is_writable(path: str) -> bool:
    return os.access(path, os.W_OK)


def _mount_id(mp: str) -> str:
    """Stable id derived from the mount point name."""
    name = os.path.basename(mp).replace(" ", "_")
    return f"ext:{name}" if name else "ext"


# ─── Storage-location classification ─────────────────────────
#
# Uses ``st_dev`` comparison instead of path-prefix heuristics.
# Two paths on the same physical device share the same ``st_dev``.


