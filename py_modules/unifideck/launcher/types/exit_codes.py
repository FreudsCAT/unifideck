from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Exit code."""
    SUCCESS = 0
    GENERIC_ERROR = 1
    CONFIG_INVALID = 2
    DEPENDENCY_MISSING = 3
    NETWORK_ERROR = 4
    CANCELLED_BY_USER = 5
    TIMED_OUT = 6
    PREFIX_CORRUPTED = 7
    GAME_FAILED = 8
    CIRCUIT_BREAKER_OPEN = 9
    SIGTERM_EQUIVALENT = 143
    def user_message_key(self) -> str:
        """User message key."""
        mapping = {
        ExitCode.SUCCESS: "",
        ExitCode.GENERIC_ERROR: "toasts.launcher.errorGeneric",
        ExitCode.CONFIG_INVALID: "toasts.launcher.errorConfig",
        ExitCode.DEPENDENCY_MISSING: "toasts.launcher.errorMissingDep",
        ExitCode.NETWORK_ERROR: "toasts.launcher.errorNetwork",
        ExitCode.CANCELLED_BY_USER: "toasts.launcher.cancelled",
        ExitCode.TIMED_OUT: "toasts.launcher.errorTimeout",
        ExitCode.PREFIX_CORRUPTED: "toasts.launcher.errorPrefix",
        ExitCode.GAME_FAILED: "toasts.launcher.errorGameFailed",
        ExitCode.CIRCUIT_BREAKER_OPEN: "toasts.launcher.errorCircuitBreakerOpen",
        ExitCode.SIGTERM_EQUIVALENT: "toasts.launcher.cancelled",
        }
        return mapping.get(self, "toasts.launcher.errorGeneric")
