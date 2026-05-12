"""Security service orchestration.

OP-19a | py_modules/unifideck/services/security/service.py

``SecurityService`` is the multi-inheritance facade composing :

* ``AuthAuditMixin``      — audit auth events;
* ``ConfigAuditMixin``    — audit config changes;
* ``TokenAuditMixin``     — audit token operations;
* ``PermissionsMixin``    — verify file permissions on sensitive paths;
* ``BruteForceDetector`` (composed, not inherited) — slow/lock auth
  attempts after consecutive failures.

Audit entries are written to the rotating audit log (``audit_log``,
OP-19b) and announced on the bus for live UI displays.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from ...core.types.events import Events
from ...event_bus.event_bus_devex import auto_wire
from ...security import DeviceFingerprint
from . import device_reset
from .audit_log import AuditLog
from .auth import AuthAuditMixin
from .bruteforce import BruteForceDetector
from .bus_emitter import emit_security_event
from .config import ConfigAuditMixin
from .config_readers import read_float, read_int, read_str
from .permissions import PermissionsMixin
from .tokens import TokenAuditMixin

if TYPE_CHECKING:
    from typing import Any
    from ...config import ConfigManager
    from ...event_bus.event_bus import EventBus
    from ...event_bus.event_replay import EventReplayBuffer
logger = logging.getLogger(__name__)
_DEFAULT_AUDIT_CAPACITY = 500
_DEFAULT_BRUTEFORCE_WINDOW_S = 60.0
_DEFAULT_BRUTEFORCE_WARNING = 5
_DEFAULT_BRUTEFORCE_ESCALATION = 20
_DEFAULT_FINGERPRINT_PATH = "~/.config/unifideck/device_fingerprint.json"


class SecurityService(
    TokenAuditMixin,
    PermissionsMixin,
    AuthAuditMixin,
    ConfigAuditMixin,
):
    """Security service."""

    def __init__(
        self,
        bus: EventBus,
        config: ConfigManager | None = None,
        fingerprint: DeviceFingerprint | None = None,
        replay: EventReplayBuffer | None = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._config = config
        self._replay = replay
        capacity = read_int(
            config,
            "security.audit_log_capacity",
            _DEFAULT_AUDIT_CAPACITY,
        )
        window_s = read_float(
            config,
            "security.bruteforce_window_seconds",
            _DEFAULT_BRUTEFORCE_WINDOW_S,
        )
        warning = read_int(
            config,
            "security.bruteforce_warning_threshold",
            _DEFAULT_BRUTEFORCE_WARNING,
        )
        escalation = read_int(
            config,
            "security.bruteforce_escalation_threshold",
            _DEFAULT_BRUTEFORCE_ESCALATION,
        )
        self._audit = AuditLog(capacity=capacity)
        self._bf = BruteForceDetector(
            window_seconds=window_s,
            warning_threshold=warning,
            escalation_threshold=escalation,
            on_threshold_crossed=self._emit_bruteforce,
        )
        self._fingerprint = fingerprint or self._build_fingerprint()
        auto_wire(self, self._bus)
        logger.info(
            "[SecurityService] initialized (audit=%d, bf=%d/%d/%gs)",
            capacity,
            warning,
            escalation,
            window_s,
        )

    async def start(self) -> None:
        """Start."""
        self._drain_config_validation_replay()
        await device_reset.check_device_fingerprint(self)

    def _drain_config_validation_replay(self) -> None:
        """Drain config validation replay."""
        if self._replay is None:
            return
        try:
            missed = self._replay.snapshot(
                events=[Events.CONFIG_VALIDATION_FAILED],
            )
            for entry in missed:
                self._audit.record(
                    "CONFIG_VALIDATION_FAILED",
                    entry.get("kwargs", {}),
                )
            if missed:
                logger.info(
                    "[SecurityService] replayed %d missed "
                    "CONFIG_VALIDATION_FAILED event(s)",
                    len(missed),
                )
        except Exception:
            logger.exception(
                "[SecurityService] replay drain failed (non-fatal)",
            )

    def _build_fingerprint(self) -> DeviceFingerprint:
        """Build fingerprint."""
        path = read_str(
            self._config,
            "security.fingerprint_path",
            _DEFAULT_FINGERPRINT_PATH,
        )
        return DeviceFingerprint(path=path)

    def _emit_bruteforce(self, *, level: str, recent_failures: int) -> None:
        """Emit bruteforce."""
        emit_security_event(
            self._bus,
            "SECURITY_BRUTEFORCE_SUSPECTED",
            level=level,
            recent_failures=recent_failures,
        )

    def get_audit_log(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Get audit log."""
        return self._audit.snapshot(limit=limit)

    def get_counters(self) -> dict[str, int]:
        """Get counters."""
        return self._audit.counters()

    def get_bruteforce_status(self) -> dict[str, Any]:
        """Get bruteforce status."""
        return self._bf.status()

    def clear_audit_log(self) -> None:
        """Clear audit log."""
        self._audit.clear()
        logger.info("[SecurityService] audit log and counters cleared")

    def reset_bruteforce_state(self) -> None:
        """Reset bruteforce state."""
        self._bf.reset()
        logger.info("[SecurityService] brute-force state reset")
