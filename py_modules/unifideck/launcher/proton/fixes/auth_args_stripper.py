from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unifideck.launcher.types.context import LaunchContext
logger = logging.getLogger(__name__)
_STRIP_PREFIXES: tuple[str, ...] = (
    "-AUTH_TYPE=",
    "-AUTH_PASSWORD=",
)
def strip_epic_auth_args(args: list[str]) -> tuple[list[str], list[str]]:
    """Strip EPIC auth args."""
    filtered: list[str] = []
    stripped: list[str] = []
    for arg in args:
        if any(arg.startswith(p) for p in _STRIP_PREFIXES):
            stripped.append(arg)
            continue
        filtered.append(arg)
    if stripped:
        logger.info(
            "[auth_args_stripper] stripped %d Epic auth args: %s",
            len(stripped),
            [s.split("=", 1)[0] + "=<redacted>" for s in stripped],
        )
    return filtered, stripped
def should_strip_for_launch_context(ctx: LaunchContext) -> bool:
    """Check whether strip for launch context."""
    try:
        store = getattr(ctx, "store", "")
        exe_path = str(getattr(ctx, "exe_path", ""))
        return (
            store == "ubisoft" and "UplayLaunch" in exe_path
        )
    except Exception:
        return False
