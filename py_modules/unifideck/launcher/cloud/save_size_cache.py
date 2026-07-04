from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
logger = logging.getLogger(__name__)
_EWMA_ALPHA = 0.3
@dataclass(frozen=True)
class CachedSize:
    """Cached size."""
    size_bytes: int
    observed_at: float
    sample_count: int
    stale: bool
def _resolve_cache_path(config: ConfigManager | None) -> str:
    """Resolve cache path."""
    if config is None or not hasattr(config, "get_str"):
        raw = "~/.cache/unifideck/cloud_save_sizes.json"
    else:
        raw = config.get_str(
            "disk_space.size_cache_path",
            "~/.cache/unifideck/cloud_save_sizes.json",
        )
    return str(Path(raw).expanduser())
def _resolve_ttl_seconds(config: ConfigManager | None) -> int:
    """Resolve TTL seconds."""
    if config is None or not hasattr(config, "get_int"):
        return 30 * 24 * 3600
    return config.get_int(
        "disk_space.size_cache_ttl_seconds", 30 * 24 * 3600,
    )
def _load(path: str) -> dict[str, Any]:
    """Load."""
    if not Path(path).is_file():
        return {}
    try:
        with Path(path).open(encoding="utf-8") as fh:
            return cast("dict[str, Any]", json.load(fh))
    except (OSError, json.JSONDecodeError) as err:
        logger.warning(
            "[save_size_cache] load failed for %s: %s — "
            "starting empty", path, err,
        )
        return {}
def _save(path: str, data: dict[str, Any]) -> None:
    """Save."""
    try:
        Path(str(Path(path).parent)).mkdir(parents=True, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with Path(tmp_path).open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        Path(tmp_path).replace(path)
    except OSError as err:
        logger.warning("[save_size_cache] save failed for %s: %s", path, err)

def get_observed_size(
    config: ConfigManager | None, store: str, game_id: str,
) -> CachedSize | None:

    """Get observed size."""
    path = _resolve_cache_path(config)
    data = _load(path)
    key = f"{store}:{game_id}"
    entry = data.get(key)
    if not entry:
        return None
    ttl = _resolve_ttl_seconds(config)
    age = time.time() - entry.get("observed_at", 0)
    return CachedSize(
        size_bytes=int(entry.get("size_bytes", 0)),
        observed_at=float(entry.get("observed_at", 0)),
        sample_count=int(entry.get("sample_count", 0)),
        stale=(age > ttl),
    )
def record_observed_size(
    config: ConfigManager | None, store: str, game_id: str, size_bytes: int,
) -> None:
    """Record observed size."""
    if size_bytes < 0:
        logger.warning(
            "[save_size_cache] negative size for %s:%s ignored",
            store, game_id,
        )
        return
    path = _resolve_cache_path(config)
    data = _load(path)
    key = f"{store}:{game_id}"
    existing = data.get(key)
    if existing is None or existing.get("sample_count", 0) == 0:
        new_size = size_bytes
        new_count = 1
    else:
        old = existing["size_bytes"]
        new_size = int(_EWMA_ALPHA * size_bytes + (1 - _EWMA_ALPHA) * old)
        new_count = existing["sample_count"] + 1
    data[key] = {
        "size_bytes": new_size,
        "observed_at": time.time(),
        "sample_count": new_count,
    }
    _save(path, data)
def measure_directory_size(directory: str) -> int:
    """Measure directory size."""
    if not Path(directory).is_dir():
        return 0
    total = 0
    try:
        for root, _, files in os.walk(directory):
            for name in files:
                try:
                    total += Path(str(Path(root) / name)).stat().st_size
                except OSError:
                    continue
    except OSError as err:
        logger.warning(
            "[save_size_cache] walk failed for %s: %s", directory, err,
        )
        return 0
    return total
