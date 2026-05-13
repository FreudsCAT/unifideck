"""Security service — orchestrate audit log, brute-force detection, fingerprints.

OP-19a | py_modules/unifideck/services/security/service.py

``SecurityService`` is the cross-cutting concern for everything
security-adjacent in Unifideck:

* **Audit logging** — every auth event, token operation,
  permissions check and config validation flows through the
  rotating ``AuditLog`` (capped at N entries, oldest evicted);
* **Brute-force detection** — repeated auth failures trigger a
  warning and eventually an escalation event for the UI;
* **Device fingerprinting** — detect device resets / theft by
  comparing a per-boot fingerprint against the cached one
  (delegates to ``device_reset``);
* **Config-validation replay** — at startup, drains any
  config-validation events that fired before this service was
  subscribed (via the bus's replay buffer).

The class itself inherits from four mixins, each providing the
``_on_*`` bus handlers for a slice of the security surface. The
audit log + brute-force detector are composed (not inherited)
because they have their own lifecycle.
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
    """Security cross-concern: audit + brute-force + fingerprint."""

    def __init__(
        self,
        bus: EventBus,
        config: ConfigManager | None = None,
        fingerprint: DeviceFingerprint | None = None,
        replay: EventReplayBuffer | None = None,
    ) -> None:
        """Wire the service and build its sub-components.

        Reads four tunables from the config:

        * ``security.audit_log_capacity`` (default 500) — how
          many recent audit entries to retain;
        * ``security.bruteforce_window_seconds`` (default 60) —
          rolling window for failure counting;
        * ``security.bruteforce_warning_threshold`` (default 5) —
          failure count that emits a warning;
        * ``security.bruteforce_escalation_threshold`` (default
          20) — failure count that emits an escalation (locks
          out for now, hard lockout in the future).

        Args:
            bus: live event bus on which security events are
                emitted and which is auto-wired for the mixin
                handlers.
            config: optional config manager.
            fingerprint: optional pre-built ``DeviceFingerprint``
                (used by tests to inject a deterministic
                fingerprint).
            replay: optional event replay buffer used to capture
                events emitted before this service was wired up.
        """
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
        """Run the async startup tasks: replay drain + fingerprint check.

        Two distinct concerns chained here:

        1. **Replay drain** — any ``CONFIG_VALIDATION_FAILED``
           events that fired before this service subscribed
           (typical ordering: ``ConfigManager`` validates at
           construction time, before ``SecurityService`` is
           wired up) are pulled from the replay buffer and
           inserted into the audit log;
        2. **Device fingerprint** — compare the current
           fingerprint against the cached one (delegates to
           ``device_reset.check_device_fingerprint``). A mismatch
           triggers a security event and potentially a
           token wipe.
        """
        self._drain_config_validation_replay()
        await device_reset.check_device_fingerprint(self)

    def _drain_config_validation_replay(self) -> None:
        """Backfill the audit log with replayed config-validation events.

        The bus's replay buffer holds the last N events emitted on
        the bus (in-memory). After this service subscribes, those
        events have already passed — without the replay we'd lose
        them. The drain pulls them out and feeds them into the
        audit log so they're not invisible to the user.

        Failures during replay drain are caught and logged but
        non-fatal — the service keeps starting up even if the
        backfill fails.
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
        """Construct a default ``DeviceFingerprint`` from config.

        Reads ``security.fingerprint_path`` from the config (with
        a sensible default under ``~/.config``). Called once at
        construction when no fingerprint was injected.

        Returns:
            A configured ``DeviceFingerprint`` instance.
        """
        path = read_str(
            self._config,
            "security.fingerprint_path",
            _DEFAULT_FINGERPRINT_PATH,
        )
        return DeviceFingerprint(path=path)

    def _emit_bruteforce(self, *, level: str, recent_failures: int) -> None:
        """Emit a brute-force suspicion event when a threshold is crossed.

        Plugged into ``BruteForceDetector`` as its
        ``on_threshold_crossed`` callback. The detector is
        synchronous, this callback is synchronous — the bus
        emission is fire-and-forget (returns a coroutine that's
        scheduled by the bus implementation).

        Args:
            level: ``"warning"`` or ``"escalation"`` — passed
                through to the event payload.
            recent_failures: failure count at the time of
                emission, for UI display.
        """
        emit_security_event(
            self._bus,
            "SECURITY_BRUTEFORCE_SUSPECTED",
            level=level,
            recent_failures=recent_failures,
        )

    def get_audit_log(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return a snapshot of the audit log entries.

        Args:
            limit: optional cap on the number of entries
                returned. ``None`` (default) returns every entry
                up to the audit log's capacity.

        Returns:
            List of audit entries newest-first.
        """
        return self._audit.snapshot(limit=limit)

    def get_counters(self) -> dict[str, int]:
        """Return per-event-kind counters from the audit log.

        Useful for the security tab in the QAM panel: shows
        totals like ``{auth_attempt: 12, auth_failure: 2,
        config_validation_failed: 1}`` at a glance.

        Returns:
            Mapping ``event_kind → count``.
        """
        return self._audit.counters()

    def get_bruteforce_status(self) -> dict[str, Any]:
        """Return the current brute-force detector state.

        Returns:
            Dict with ``recent_failures``, ``window_seconds``,
            ``warning_threshold``, ``escalation_threshold``.
            Used by the QAM UI to show the live counter.
        """
        return self._bf.status()

    def clear_audit_log(self) -> None:
        """Empty the audit log and reset its counters.

        Called from the RPC layer when the user clicks "clear
        audit log" in the QAM. Loud INFO log so the cleanup is
        traceable in plugin logs (defence: a hostile user
        couldn't silently wipe their tracks).
        """
        self._audit.clear()
        logger.info("[SecurityService] audit log and counters cleared")

    def reset_bruteforce_state(self) -> None:
        """Clear every recent failure from the brute-force detector.

        Called from the RPC layer (admin reset) and after a
        successful legitimate auth (the user just proved they're
        not the brute-forcer). Same INFO-level logging policy as
        ``clear_audit_log``.
        """
        self._bf.reset()
        logger.info("[SecurityService] brute-force state reset")
