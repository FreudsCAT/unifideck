from __future__ import annotations

import contextlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
logger = logging.getLogger(__name__)
_DEFAULT_MIN_FREE_BYTES = 1024 * 1024 * 1024
@dataclass(frozen=True)
class DiskSpaceCheck:
    """Disk space check."""
    has_space: bool
    free_bytes: int
    required_bytes: int
    path: Path
class LowDiskSpaceError(Exception):
    """Low disk space error."""
    def __init__(
        self,
        message: str,
        free_bytes: int,
        required_bytes: int,
    ) -> None:
        """Initialize the instance."""
        super().__init__(message)
        self.free_bytes = free_bytes
        self.required_bytes = required_bytes
def get_min_free_bytes(config: ConfigManager | None) -> int:
    """Get min free bytes."""
    if config is None or not hasattr(config, "get_int"):
        return _DEFAULT_MIN_FREE_BYTES
    return config.get_int(
        "disk_space.min_free_bytes", _DEFAULT_MIN_FREE_BYTES,
    )
def check_disk_space(
    path: Path, required_bytes: int,
) -> DiskSpaceCheck:
    """Check disk space."""
    probe = path
    while probe != probe.parent and not probe.exists():
        probe = probe.parent
    try:
        usage = shutil.disk_usage(str(probe))
        return DiskSpaceCheck(
            has_space=usage.free >= required_bytes,
            free_bytes=usage.free,
            required_bytes=required_bytes,
            path=probe,
        )
    except OSError as err:
        logger.warning(
            "[disk_space] disk_usage(%s) failed: %s — assuming no space",
            probe, err,
        )
        return DiskSpaceCheck(
            has_space=False,
            free_bytes=0,
            required_bytes=required_bytes,
            path=probe,
        )

def assert_enough_space(
    path: Path, config: ConfigManager | None,
    *, store: str | None = None,
    game_id: str | None = None,
) -> None:

    """Assert enough space."""
    required = get_min_free_bytes(config)
    if store and game_id:
        with contextlib.suppress(ImportError):
            from .save_size_cache import get_observed_size
            cached = get_observed_size(config, store, game_id)
            if cached is not None and not cached.stale:
                multiplier = _get_multiplier(config)
                refined = int(cached.size_bytes * multiplier)
                required = max(required, refined)
    check = check_disk_space(path, required)
    if not check.has_space:
        raise LowDiskSpaceError(
            f"low disk space at {check.path}: "
            f"have {check.free_bytes} bytes, need {required}",
            free_bytes=check.free_bytes,
            required_bytes=required,
        )
def _get_multiplier(config: ConfigManager | None) -> float:
    """Get multiplier."""
    if config is None or not hasattr(config, "get_float"):
        return 1.5
    return cast("float", config.get_float("disk_space.size_multiplier", 1.5))
