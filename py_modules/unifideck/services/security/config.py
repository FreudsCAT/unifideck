"""services.security.mixins.config — Config validation audit handlers.

Two @subscribe handlers that observe ``ConfigValidator`` events
at plugin boot:

  - CONFIG_VALIDATION_COMPLETED : clean validation (happy path)
  - CONFIG_VALIDATION_FAILED    : schema violations → plugin
                                  enters degraded mode

These events can fire before this service is subscribed (since
validation runs before ``bootstrap_services``). The
``SecurityService.start()`` hook drains the EventReplayBuffer
for missed CONFIG_VALIDATION_FAILED events to ensure boot-time
config failures are still recorded in the audit log.

Mixed into ``SecurityService`` via multiple inheritance so the
@subscribe decorators are picked up by ``auto_wire``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from .audit_log import AuditLog

logger = logging.getLogger(__name__)


class ConfigAuditMixin:
    """Record + react to ConfigValidator lifecycle events.

    Expects the host class to provide:

      - ``self._audit`` : ``AuditLog`` instance
    """

    _audit: AuditLog

    @subscribe(Events.CONFIG_VALIDATION_COMPLETED)
    async def _on_config_validation_completed(
        self, **kwargs: Any,
    ) -> None:
        """Record a successful config validation at boot.

        Emitted by ConfigValidator after a clean schema
        validation of defaults/config.json at plugin boot.
        Visible in the audit log as a normal event so operators
        can confirm the plugin started with a valid config. No
        warning level: this is the happy path.
        """
        self._audit.record("CONFIG_VALIDATION_COMPLETED", kwargs)
        logger.info(
            "[SecurityService] config validation completed "
            "(defaults_validated=%s, user_overrides=%s)",
            kwargs.get("defaults_validated", False),
            kwargs.get("user_overrides_present", False),
        )

    @subscribe(Events.CONFIG_VALIDATION_FAILED)
    async def _on_config_validation_failed(
        self, **kwargs: Any,
    ) -> None:
        """Record a failed config validation at boot.

        Emitted by ConfigValidator when the defaults or user
        overrides file fails schema validation. Logged at
        warning level because the plugin is running in degraded
        mode. The DiagnosticsPanel reads the full error list
        via the get_config_validation_status RPC; this handler
        only records the summary counts in the audit log to
        surface the problem in the Security counters and
        timeline.
        """
        self._audit.record("CONFIG_VALIDATION_FAILED", kwargs)
        logger.warning(
            "[SecurityService] config validation failed: "
            "%d error(s), first at %s (source=%s)",
            kwargs.get("error_count", 0),
            kwargs.get("first_error_path", "<unknown>"),
            kwargs.get("first_error_source", "<unknown>"),
        )
