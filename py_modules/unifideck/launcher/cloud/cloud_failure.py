from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from ...core.types.events import Events
if TYPE_CHECKING:
    from ...config import ConfigManager
    from ...event_bus.event_bus import EventBus
logger = logging.getLogger(__name__)
def classify_cloud_error(err: BaseException) -> str:
    """Classify cloud error."""
    try:
        import aiohttp
    except ImportError:
        aiohttp = None
    if aiohttp is not None:
        if isinstance(err, aiohttp.ClientConnectionError):
            return "network_unreachable"
        if isinstance(err, aiohttp.ClientResponseError):
            status = getattr(err, "status", 0)
            if status == 401:
                return "auth_expired"
            if status == 403:
                return "forbidden"
            if status == 413:
                return "quota_exceeded"
            if 500 <= status < 600:
                return "server_error"
        if isinstance(err, aiohttp.ClientTimeout):
            return "timed_out"
    if isinstance(err, OSError):
        import errno
        if err.errno == errno.ENOSPC:
            return "disk_full"
        if err.errno in (errno.EACCES, errno.EPERM):
            return "permission_denied"
    try:
        from .disk_space import LowDiskSpaceError
        if isinstance(err, LowDiskSpaceError):
            return "disk_space_low"
    except ImportError:
        pass
    try:
        import asyncio
        if isinstance(err, asyncio.CancelledError):
            return "cancelled"
    except ImportError:
        pass
    return "unknown"

_DEFAULT_BEHAVIOR = "toast"
_VALID_BEHAVIORS = frozenset({"silent", "toast"})
def get_failure_behavior(config: ConfigManager | None, store: str) -> str:
    """Get failure behavior."""
    if config is None or not hasattr(config, "get_str"):
        return _DEFAULT_BEHAVIOR
    key = f"cloud.failure_behavior.{store}"
    raw = config.get_str(key, "")
    if not raw:
        raw = config.get_str(
            "cloud.failure_behavior.default", _DEFAULT_BEHAVIOR,
        )
    if raw not in _VALID_BEHAVIORS:
        logger.warning(
            "[cloud_failure] invalid behavior %r for store %s, "
            "falling back to %r", raw, store, _DEFAULT_BEHAVIOR,
        )
        return _DEFAULT_BEHAVIOR
    return raw
async def handle_cloud_sync_failure(
    bus: EventBus,
    config: ConfigManager | None,
    *,
    phase: str,
    store: str,
    game_id: str,
    error: BaseException,
) -> None:
    """Handle cloud sync failure."""
    code = classify_cloud_error(error)
    behavior = get_failure_behavior(config, store)
    logger.error(
        "[cloud_failure] phase=%s store=%s game_id=%s "
        "error_code=%s behavior=%s error=%s",
        phase, store, game_id, code, behavior, error,
        exc_info=error,
    )
    if behavior == "silent":
        return
    await _emit_toast(bus, phase=phase, store=store, game_id=game_id, code=code)
async def _emit_toast(
    bus: EventBus, *,
    phase: str, store: str, game_id: str, code: str,
) -> None:
    """Emit toast."""
    if bus is None:
        return
    i18n_key = (
        "toasts.launcher.cloudSyncDownFailed"
        if phase == "sync_down"
        else "toasts.launcher.cloudSyncUpFailed"
    )
    payload: dict[str, Any] = {
        "severity": "warning",
        "i18n_key": i18n_key,
        "i18n_params": {
            "store": store,
            "error_code": code,
            "error_i18n_key": f"cloudSync.error.{code}",
        },
        "duration_ms": 6000,
        "game_id": game_id,
        "store": store,
        "phase": phase,
    }
    resolved_action = _resolve_toast_action(
        code, store=store, game_id=game_id, phase=phase,
    )
    if resolved_action is not None:
        payload["action"] = resolved_action
    try:
        await bus.emit(Events.LAUNCHER_STAGE, **payload)
    except Exception:
        logger.exception("[cloud_failure] toast emit failed")

def _resolve_toast_action(
    code: str, *, store: str, game_id: str, phase: str,
) -> dict[str, str] | None:

    """Resolve toast action."""
    action = _TOAST_ACTIONS.get(code)
    if action is None:
        return None
    ctx_vars = {"store": store, "game_id": game_id, "phase": phase}
    working: dict[str, str] = dict(action)
    for url_key in ("target_url", "fallback_url"):
        if url_key not in working:
            continue
        try:
            working[url_key] = working[url_key].format(**ctx_vars)
        except (KeyError, IndexError) as err:
            logger.warning(
                "[cloud_failure] action template error for "
                "code=%s key=%s: %s — dropping action",
                code, url_key, err,
            )
            return None
    return working
_TOAST_ACTIONS = {
    "disk_space_low": {
        "i18n_label_key": "toasts.actions.openStorageManager",
        "target_url": "steam://settings/storage",
        "fallback_url": "steam://settings",
    },
    "auth_expired": {
        "i18n_label_key": "toasts.actions.signInToStore",
        "target_url": "unifideck://auth/{store}",
    },
    "network_unreachable": {
        "i18n_label_key": "toasts.actions.retrySync",
        "target_url": "unifideck://retry-sync/{store}/{game_id}/{phase}",
    },
    "timed_out": {
        "i18n_label_key": "toasts.actions.retrySync",
        "target_url": "unifideck://retry-sync/{store}/{game_id}/{phase}",
    },
    "server_error": {
        "i18n_label_key": "toasts.actions.retrySync",
        "target_url": "unifideck://retry-sync/{store}/{game_id}/{phase}",
    },
    "unknown": {
        "i18n_label_key": "toasts.actions.openSaveFolder",
        "target_url": "unifideck://open-save-folder/{store}/{game_id}",
    },
    "permission_denied": {
        "i18n_label_key": "toasts.actions.openSaveFolder",
        "target_url": "unifideck://open-save-folder/{store}/{game_id}",
    },
    "cancelled": {
        "i18n_label_key": "toasts.actions.openSaveFolder",
        "target_url": "unifideck://open-save-folder/{store}/{game_id}",
    },
}