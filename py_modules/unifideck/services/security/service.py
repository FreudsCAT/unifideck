r"""services.security.service — SecurityService facade class.

Reactive audit + policy enforcement service. Composes three
cohesive units (``AuditLog``, ``BruteForceDetector``,
``DeviceFingerprint``) and inherits from four thematic mixins
that each carry 1-5 ``@subscribe``-decorated handlers.

The ``auto_wire(self, bus)`` call in ``__init__`` picks up all
12 handlers transparently. See the subpackage docstring
(``__init__.py``) for the full module layout and policy
catalogue.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import auto_wire
from unifideck.security import DeviceFingerprint

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

    from unifideck.config import ConfigManager
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.event_bus.event_replay import EventReplayBuffer

logger = logging.getLogger(__name__)


# Config defaults mirrored from ``defaults/config.json``. Read by
# ``__init__`` via ``config_readers`` with graceful fallback when
# ``config`` is None (tests, subset bootstrap).
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
    """Reactive audit log + policy enforcement for security events.

    Exposes ``get_audit_log()``, ``get_counters()``,
    ``get_bruteforce_status()``, ``clear_audit_log()``,
    ``reset_bruteforce_state()`` for operator inspection via RPC.
    All handlers are best-effort.
    """

    # ══════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════

    def __init__(
        self,
        bus: EventBus,
        config: ConfigManager | None = None,
        fingerprint: DeviceFingerprint | None = None,
        replay: EventReplayBuffer | None = None,
    ) -> None:
        """Initialise the service and wire its handlers.

        ``replay`` is the plugin's ``EventReplayBuffer``, used by
        ``start()`` to drain any ``CONFIG_VALIDATION_FAILED``
        events emitted before this service had a chance to
        subscribe (the normal case since validation runs before
        ``bootstrap_services``). May be None in tests and subset
        bootstraps — ``start()`` simply skips the drain step.
        """
        self._bus = bus
        self._config = config
        self._replay = replay

        # Read tunables once so we can log them without re-querying
        # the detector after construction.
        capacity = read_int(
            config, "security.audit_log_capacity",
            _DEFAULT_AUDIT_CAPACITY,
        )
        window_s = read_float(
            config, "security.bruteforce_window_seconds",
            _DEFAULT_BRUTEFORCE_WINDOW_S,
        )
        warning = read_int(
            config, "security.bruteforce_warning_threshold",
            _DEFAULT_BRUTEFORCE_WARNING,
        )
        escalation = read_int(
            config, "security.bruteforce_escalation_threshold",
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
            capacity, warning, escalation, window_s,
        )

    async def start(self) -> None:
        """Lifecycle hook called by ``start_async_services``.

        Two startup tasks in order:

        1. **Replay drain** — pulls any ``CONFIG_VALIDATION_FAILED``
           events that fired before this service was wired,
           preserving the boot-time audit trail.
        2. **Device fingerprint verification** — verify the stored
           fingerprint against the current device. On mismatch,
           wipe configured token files and emit
           ``SECURITY_DEVICE_RESET_DETECTED``.

        Never raises: handlers are defensive and leave the service
        operational with a reduced feature set rather than
        aborting the plugin boot.
        """
        self._drain_config_validation_replay()
        await device_reset.check_device_fingerprint(self)

    def _drain_config_validation_replay(self) -> None:
        """Record any ``CONFIG_VALIDATION_FAILED`` events we missed.

        Before the ServiceContainer migration this service lived in
        ``_build_eventbus_pipeline`` and was subscribed by the time
        ``_validate_config`` ran. After the migration services are
        wired AFTER validation, so direct subscribers would miss
        the event. The replay buffer snapshot lets us catch up
        without forcing ``bootstrap_services`` to reorder.
        """
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
        """Construct a ``DeviceFingerprint`` from config-provided path."""
        path = read_str(
            self._config, "security.fingerprint_path",
            _DEFAULT_FINGERPRINT_PATH,
        )
        return DeviceFingerprint(path=path)

    def _emit_bruteforce(
        self, *, level: str, recent_failures: int,
    ) -> None:
        """Fire ``SECURITY_BRUTEFORCE_SUSPECTED`` on threshold cross.

        Wired as the ``on_threshold_crossed`` callback of the
        ``BruteForceDetector`` so this service stays the sole
        emitter of ``SECURITY_*`` events (the detector itself is
        bus-agnostic).
        """
        emit_security_event(
            self._bus, "SECURITY_BRUTEFORCE_SUSPECTED",
            level=level, recent_failures=recent_failures,
        )

    # ══════════════════════════════════════════════════════════
    # Public API (exposed via RPC)
    # ══════════════════════════════════════════════════════════

    def get_audit_log(
        self, limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return a snapshot of the audit log, newest first."""
        return self._audit.snapshot(limit=limit)

    def get_counters(self) -> dict[str, int]:
        """Return a snapshot of the per-event-type counters."""
        return self._audit.counters()

    def get_bruteforce_status(self) -> dict[str, Any]:
        """Return the current state of the brute-force detector."""
        return self._bf.status()

    def clear_audit_log(self) -> None:
        """Wipe the audit log and reset counters.

        Does NOT reset the brute-force detector state on purpose:
        an attacker could otherwise clear their tracks. Use
        ``reset_bruteforce_state()`` explicitly after operator
        review.
        """
        self._audit.clear()
        logger.info("[SecurityService] audit log and counters cleared")

    def reset_bruteforce_state(self) -> None:
        """Clear the brute-force detector after operator review."""
        self._bf.reset()
        logger.info("[SecurityService] brute-force state reset")
