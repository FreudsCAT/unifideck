"""Config audit mixin — log config changes.

OP-19j | py_modules/unifideck/services/security/config.py

``ConfigAuditMixin`` audits writes to the user config — useful to
trace back unexpected behaviour to a config change. Logs the
modified key, old vs new value (redacted if the key matches the
"sensitive" pattern), and the timestamp.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from ...core.types.events import Events
from ...event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from .audit_log import AuditLog
logger = logging.getLogger(__name__)


class ConfigAuditMixin:
    """Config-validation bus subscriptions for the audit log.

    Despite the module name, this mixin doesn't audit
    config-write operations — those happen synchronously inside
    ``ConfigManager.set`` without emitting events. What it
    actually audits is the **validation outcome** at startup
    (and on user-triggered revalidations).
    """

    _audit: AuditLog

    @subscribe(Events.CONFIG_VALIDATION_COMPLETED)
    async def _on_config_validation_completed(self, **kwargs: Any) -> None:
        """Record a clean config-validation pass.

        Emitted by ``ConfigManager`` after every successful
        validation. Logged at INFO with the two boolean
        outcomes (defaults validated, user overrides present)
        so the plugin log shows the config state at every boot.
        """
        self._audit.record("CONFIG_VALIDATION_COMPLETED", kwargs)
        logger.info(
            "[SecurityService] config validation completed "
            "(defaults_validated=%s, user_overrides=%s)",
            kwargs.get("defaults_validated", False),
            kwargs.get("user_overrides_present", False),
        )

    @subscribe(Events.CONFIG_VALIDATION_FAILED)
    async def _on_config_validation_failed(self, **kwargs: Any) -> None:
        """Record a config-validation failure with the first error details.

        Surface the error count, first-error path and source
        right in the log line — gives the user enough context
        to find the broken key in their config without needing
        to dig through the full payload.
        """
        self._audit.record("CONFIG_VALIDATION_FAILED", kwargs)
        logger.warning(
            "[SecurityService] config validation failed: "
            "%d error(s), first at %s (source=%s)",
            kwargs.get("error_count", 0),
            kwargs.get("first_error_path", "<unknown>"),
            kwargs.get("first_error_source", "<unknown>"),
        )
