from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.config import ConfigManager
logger = logging.getLogger(__name__)

# Root logger whose subtree is captured into the per-launch archive.
# Must be the whole ``unifideck`` tree, not just ``unifideck.launcher``:
# the launch pipeline logs under ``unifideck.services.launcher.*``
# (orchestrator / helpers), ``unifideck.stores.*`` and
# ``unifideck.compatibility.*`` too, so a failure raised there (e.g.
# "umu-run not found", DependencyMissingError in helpers) would
# otherwise never reach the archive — making launches look silent.
_ARCHIVE_LOGGER_NAME = "unifideck"
def _resolve_archive_dir(config: ConfigManager | None) -> Path:
    """Resolve archive dir."""
    if config is None or not hasattr(config, "get_str"):
        raw = "~/.local/share/unifideck/launches"
    else:
        raw = config.get_str(
            "logs.archive_path",
            "~/.local/share/unifideck/launches",
        )
    path = Path(str(Path(raw).expanduser()))
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        logger.warning(
            "[log_archive] failed to create %s: %s — archiving disabled",
            path, err,
        )
    return path
def _resolve_retention_seconds(config: ConfigManager | None) -> int:
    """Resolve retention seconds."""
    if config is None or not hasattr(config, "get_int"):
        return 7 * 24 * 3600
    return config.get_int("logs.retention_days", 7) * 24 * 3600
def prune_old_launches(config: ConfigManager | None) -> int:
    """Prune old launches."""
    archive_dir = _resolve_archive_dir(config)
    if not archive_dir.is_dir():
        return 0
    cutoff = time.time() - _resolve_retention_seconds(config)
    removed = 0
    try:
        for entry in archive_dir.iterdir():
            if not entry.is_file() or entry.suffix != ".log":
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError as err:
                logger.warning(
                    "[log_archive] failed to prune %s: %s",
                    entry, err,
                )
    except OSError as err:
        logger.warning(
            "[log_archive] failed to scan %s: %s", archive_dir, err,
        )
    if removed > 0:
        logger.info(
            "[log_archive] pruned %d expired launch log(s)", removed,
        )
    return removed

def attach_launch_handler(
    launch_id: str, config: ConfigManager | None,
    *, min_level: int = logging.INFO,
) -> logging.Handler | None:

    """Attach launch handler."""
    archive_dir = _resolve_archive_dir(config)
    path = archive_dir / f"{launch_id}.log"
    try:
        handler = logging.FileHandler(str(path), encoding="utf-8")
        handler.setLevel(min_level)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        ))
        logging.getLogger(_ARCHIVE_LOGGER_NAME).addHandler(handler)
        return handler
    except OSError as err:
        logger.warning(
            "[log_archive] failed to attach handler for %s: %s",
            path, err,
        )
        return None
def detach_launch_handler(handler: logging.Handler | None) -> None:
    """Detach launch handler."""
    if handler is None:
        return
    try:
        logging.getLogger(_ARCHIVE_LOGGER_NAME).removeHandler(handler)
        handler.close()
    except Exception:
        logger.exception("[log_archive] detach handler failed")
def read_launch_logs(
    launch_id: str, config: ConfigManager | None,
    *, max_lines: int = 500,
) -> dict[str, Any]:
    """Read launch logs."""
    archive_dir = _resolve_archive_dir(config)
    path = archive_dir / f"{launch_id}.log"
    result = {
        "exists": False, "path": str(path), "lines": [],
        "total": 0,
    }
    if not path.is_file():
        return result
    result["exists"] = True
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            raw = fh.readlines()
    except OSError as err:
        logger.warning("[log_archive] read %s failed: %s", path, err)
        return result
    result["total"] = len(raw)
    tail = raw[-max_lines:] if len(raw) > max_lines else raw
    parsed = []
    for line in tail:
        level = "INFO"
        if "[ERROR]" in line or "[CRITICAL]" in line:
            level = "ERROR"
        elif "[WARNING]" in line:
            level = "WARNING"
        elif "[DEBUG]" in line:
            level = "DEBUG"
        parsed.append({"level": level, "text": line.rstrip("\n")})
    result["lines"] = parsed
    return result

def export_launch_logs(
    launch_id: str, dest_path: str, config: ConfigManager | None,
) -> dict[str, Any]:

    """Export launch logs."""
    import shutil
    archive_dir = _resolve_archive_dir(config)
    src = archive_dir / f"{launch_id}.log"
    if not src.is_file():
        return {
            "success": False,
            "error": "source_missing",
            "dest_path": None,
        }
    dst = Path(str(Path(dest_path).expanduser()))
    if not dst.is_absolute():
        dst = Path.home() / dst
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
    except OSError as err:
        logger.warning(
            "[log_archive] export %s → %s failed: %s",
            src, dst, err,
        )
        return {
            "success": False,
            "error": str(err),
            "dest_path": str(dst),
        }
    return {
        "success": True,
        "dest_path": str(dst),
        "error": None,
    }
