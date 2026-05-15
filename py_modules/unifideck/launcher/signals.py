from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
CLEANUP_PATTERNS = (
    "steam-runtime-launch-client",
    "umu-run",
)
@dataclass
class SignalState:
    """Signal state."""
    terminated_by_signal: bool = False
    pending_pids: set[int] = field(default_factory=set)
class GameProcessRegistry:
    """Game process registry."""
    def __init__(self, state: SignalState) -> None:
        """Initialize the instance."""
        self._state = state
    def track(self, proc: subprocess.Popen[bytes]) -> None:
        """Track."""
        if proc.pid:
            self._state.pending_pids.add(proc.pid)
    def untrack(self, proc: subprocess.Popen[bytes]) -> None:
        """Untrack."""
        self._state.pending_pids.discard(proc.pid)
    def terminate_all(self) -> None:
        """Terminate all."""
        self._state.terminated_by_signal = True
        for pid in list(self._state.pending_pids):
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            except OSError:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGTERM)
        for pattern in CLEANUP_PATTERNS:
            with contextlib.suppress(FileNotFoundError, subprocess.TimeoutExpired):
                subprocess.run(
                    ["pkill", "-TERM", "-f", pattern],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    check=False,  # pkill rc=1 on "no match" is expected
                )

def install_signal_handlers(
    registry: GameProcessRegistry,
) -> SignalState:

    """Install signal handlers."""
    state = registry._state
    def _handler(signum: int, _frame: object | None) -> None:
        """Handler."""
        logger.info(
            "[launcher.signals] received signal %d, terminating games",
            signum,
        )
        registry.terminate_all()
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    return state
