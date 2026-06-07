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

The ``/proc/mounts`` scan and per-device directory creation are
blocking filesystem work, so the async RPCs delegate to the
module-level sync builders (``_build_storage_locations`` /
``_build_browseable_devices``) through a single
``asyncio.to_thread`` hop rather than touching disk on the event
loop.
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
        config = getattr(self, "config", None)
        custom_path = _read_config_str(config, "download.custom_path")
        default = (
            _read_config_str(config, "download.default_location", "internal")
            or "internal"
        )
        locations = await asyncio.to_thread(_build_storage_locations, custom_path)
        return {"locations": locations, "default": default}

    async def get_browseable_devices(self) -> Any:
        """Return mount-points of every writable storage device.

        Reads ``/proc/mounts`` to discover real mount points with
        no hardcoded paths.  Internal = ``$HOME``; external = every
        other writable, non-system mount that sits on a different
        device.
        """
        devices = await asyncio.to_thread(_build_browseable_devices)
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


# ─── Storage builders (blocking — run via asyncio.to_thread) ──────


def _build_storage_locations(custom_path: str | None) -> list[dict[str, Any]]:
    """Enumerate one location per writable device + optional custom path.

    Internal storage (``~/Games``) is always first; each distinct
    external device contributes one ``Games/`` entry; a configured
    custom path is appended last.
    """
    home_dev = _device_id(str(Path.home()))
    games_root = str(Path("~/Games").expanduser())
    _ensure_dir(games_root)
    locations: list[dict[str, Any]] = [
        _location_entry("internal", "Internal storage", games_root, games_root),
    ]
    for mp, _dev, name in _external_mounts(home_dev):
        locations.append(
            _location_entry("sdcard", name, _ensure_games_subdir(mp), mp),
        )
    if custom_path:
        locations.append(
            _location_entry("custom", custom_path, custom_path, custom_path),
        )
    return locations


def _build_browseable_devices() -> list[dict[str, Any]]:
    """List every writable device's mount point for the file picker."""
    home = str(Path.home())
    devices: list[dict[str, Any]] = [
        {
            "id": "internal",
            "label": "Internal Storage",
            "path": home,
            "free_space_gb": _free_gb(home),
        },
    ]
    for mp, _dev, name in _external_mounts(_device_id(home)):
        devices.append({
            "id": _mount_id(mp),
            "label": name,
            "path": mp,
            "free_space_gb": _free_gb(mp),
        })
    return devices


def _external_mounts(home_dev: int) -> list[tuple[str, int, str]]:
    """Return ``(mount_point, st_dev, label)`` for each external device.

    Deduplicated by device; the device hosting ``$HOME`` is
    excluded. Pure blocking I/O — call from a thread.
    """
    found: list[tuple[str, int, str]] = []
    seen: set[int] = {home_dev}
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError as e:
        logger.debug("[storage] /proc/mounts read failed: %s", e)
        return found
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mp, fstype = parts[1], parts[2]
        if not _is_eligible_mount(mp, fstype):
            continue
        dev = _device_id(mp)
        if dev == 0 or dev in seen or not _is_writable(mp):
            continue
        seen.add(dev)
        logger.info("[storage] external mount candidate: %s (dev=%s)", mp, dev)
        found.append((mp, dev, Path(mp).name or mp))
    return found


# ─── Module-level helpers ─────────────────────────────────────


def _is_eligible_mount(mp: str, fstype: str) -> bool:
    """True if *mp* is a real, non-virtual, mountable directory."""
    if fstype in _SKIP_FSTYPES or mp.startswith(_VIRTUAL_PREFIXES):
        return False
    return Path(mp).is_dir()


def _ensure_games_subdir(mp: str) -> str:
    """Return ``<mp>/Games`` (created if needed), or *mp* on failure."""
    games = Path(mp) / "Games"
    try:
        games.mkdir(parents=True, exist_ok=True)
    except OSError:
        return mp
    return str(games)


def _location_entry(
    loc_id: str, label: str, path: str, free_basis: str,
) -> dict[str, Any]:
    """Build a storage-location dict; free space is measured on *free_basis*."""
    return {
        "id": loc_id,
        "label": label,
        "path": path,
        "available": True,
        "free_space_gb": _free_gb(free_basis),
    }


def _read_config_str(
    config: Any, key: str, default: str | None = None,
) -> str | None:
    """Read a string config value defensively; *default* on any failure."""
    if config is None:
        return default
    try:
        value = config.get(key, default)
    except Exception as e:
        logger.debug("[storage] reading %s failed: %s", key, e)
        return default
    return value if isinstance(value, str) else default


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
        return Path(path).stat().st_dev
    except OSError:
        return 0


def _is_writable(path: str) -> bool:
    return os.access(path, os.W_OK)


def _mount_id(mp: str) -> str:
    """Stable id derived from the mount point name."""
    name = Path(mp).name.replace(" ", "_")
    return f"ext:{name}" if name else "ext"
