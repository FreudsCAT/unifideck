"""ObservabilityRPCMixin — diagnostics + metrics + runtime probes RPC.

OP-26k | py_modules/unifideck/rpc/mixins/observability.py

Mixin equivalent of ``ObservabilityHandlers`` (OP-25e). Same
seven public methods + four private ``_collect_*`` helpers.

Key differences vs the handler-group version:

* The mixin reaches for ``self.dispatcher`` / ``self.watchdog``
  / ``self.latency`` / ``self.replay`` directly on the host
  (older composition) — there's no ``set_bus_collaborators``
  hook.
* ``_log_probe`` is a module-level helper rather than an
  instance method.
* ``get_plugin_metrics`` calls ``services.metrics.snapshot()``
  instead of ``get_plugin_metrics()`` — older method name.
"""

from __future__ import annotations

import logging
from typing import Any

from unifideck.core.types import Events
from unifideck.rpc import RpcError

logger = logging.getLogger(__name__)


class ObservabilityRPCMixin:
    """Diagnostics + metrics RPC, mixed into the plugin class."""

    bus: Any
    services: Any
    dispatcher: Any
    watchdog: Any
    latency: Any
    replay: Any
    runtime_probes: Any

    async def get_plugin_metrics(self) -> Any:
        """Return the plugin-wide metrics snapshot.

        Delegates to ``MetricsCollector.snapshot``
        (older method name) which aggregates per-store
        sync metrics, download counts, and launch counts.

        Returns:
            Metrics dict from the collector.
        """
        return self.services.metrics.snapshot()

    async def get_bus_health(self) -> Any:
        """Compose a unified bus-health snapshot across every collaborator.

        Workflow:

        1. Start from ``self.bus.health()`` (must be
           present on this older composition path; no
           ``getattr`` fallback like the handler group).
        2. Layer in dispatcher, watchdog, latency, replay,
           and runtime-probes sections — each optional and
           guarded by a ``hasattr`` check.

        Returns:
            Multi-section dict whose shape depends on which
            collaborators are present.
        """
        health = self.bus.health()
        self._collect_dispatcher_metrics(health)
        self._collect_watchdog_metrics(health)
        self._collect_latency_metrics(health)
        self._collect_misc_metrics(health)
        return health

    async def subscribe_replay(self, events: list) -> Any:
        """Return a snapshot of the replay buffer filtered to ``events``.

        Despite the "subscribe" name, this is a one-shot
        snapshot read (not a live subscription) — the
        frontend polls when the diagnostics panel is open.

        Args:
            events: list of event types to include.

        Returns:
            List of replay entry dicts, or empty list if
            no replay buffer is wired.
        """
        if not hasattr(self, "replay") or self.replay is None:
            return []
        return self.replay.snapshot(events=events)

    async def release_quarantine(self, handler_name: str) -> Any:
        """Release a quarantined handler from the watchdog.

        Used by the "release handler" button. Returns the
        watchdog's outcome (``True`` if a quarantine was
        actually cleared).

        Args:
            handler_name: handler identifier (typically
                ``ClassName.method_name``).

        Returns:
            ``{success: bool, error?: str}`` — ``error`` is
            only set when the watchdog itself is missing.
        """
        if not hasattr(self, "watchdog") or self.watchdog is None:
            return {"success": False, "error": "watchdog not wired"}
        released = self.watchdog.release_quarantine(handler_name)
        return {"success": released}

    async def get_feature_flags(self) -> Any:
        """Return the current feature-flag state.

        Returns:
            ``{feature_name → bool}`` from
            ``FeatureFlagService.get_flags``.

        Raises:
            RpcError: ``service_unavailable`` if the
                feature-flag service isn't wired.
        """
        if self.services.feature_flags is None:
            raise RpcError(
                "service_unavailable",
                service="feature_flags",
            )
        return self.services.feature_flags.get_flags()

    async def get_probe_history(self) -> Any:
        """Return the rolling history of past runtime-probe reports.

        Used by the diagnostics panel's "probe history"
        view.

        Returns:
            List of probe-history entries (newest last).

        Raises:
            RpcError: ``service_unavailable`` if the
                probe-reaction service isn't wired.
        """
        if self.services.probe_reaction is None:
            raise RpcError(
                "service_unavailable",
                service="probe_reaction",
            )
        return self.services.probe_reaction.get_history()

    async def report_runtime_probes(self, probes: list) -> Any:
        """Inbound: frontend posts the result of its own probe run.

        Workflow:

        1. Validate input shape (must be a list).
        2. Cache the report on ``self.runtime_probes`` so
           ``_collect_misc_metrics`` can include it in
           ``get_bus_health``.
        3. Log every probe at its declared severity via the
           module-level ``_log_probe`` helper.
        4. Emit ``RUNTIME_PROBES_REPORTED`` on the bus —
           failures absorbed at DEBUG.

        Args:
            probes: list of probe dicts with at minimum
                ``name``, ``severity``, ``message``.

        Returns:
            ``{ok, count, has_errors}`` summary.
        """
        if not isinstance(probes, list):
            return {"ok": False, "error": "probes must be a list"}
        self.runtime_probes = probes
        for p in probes:
            _log_probe(p)
        try:
            await self.bus.emit(
                Events.RUNTIME_PROBES_REPORTED,
                probes=probes,
            )
        except Exception:
            logger.debug("[runtime-probe] bus emit failed")
        has_errors = any(p.get("severity") == "error" for p in probes)
        return {
            "ok": True,
            "count": len(probes),
            "has_errors": has_errors,
        }

    def _collect_dispatcher_metrics(self, health: dict) -> None:
        """Add a ``dispatcher`` section to ``health`` if dispatcher is wired.

        Pulls a fresh ``DispatcherMetrics`` snapshot and
        flattens the five counter fields onto ``health``.
        ``hasattr`` check protects against early-boot
        states where the dispatcher slot hasn't been
        assigned yet.

        Args:
            health: the health dict being assembled
                (mutated in place).
        """
        if not hasattr(self, "dispatcher") or self.dispatcher is None:
            return
        m = self.dispatcher.get_metrics()
        health["dispatcher"] = {
            "emitted_total": m.emitted_total,
            "dispatched_total": m.dispatched_total,
            "coalesced_total": m.coalesced_total,
            "dropped_background_total": m.dropped_background_total,
            "pending_by_priority": m.pending_by_priority,
        }

    def _collect_watchdog_metrics(self, health: dict) -> None:
        """Add a ``watchdog`` section listing every handler's state.

        Args:
            health: the health dict being assembled.
        """
        if not hasattr(self, "watchdog") or self.watchdog is None:
            return
        health["watchdog"] = {
            name: {
                "invocations": s.invocations,
                "timeouts": s.timeouts,
                "consecutive_timeouts": s.consecutive_timeouts,
                "quarantined": s.quarantined,
            }
            for name, s in self.watchdog.get_metrics().items()
        }

    def _collect_latency_metrics(self, health: dict) -> None:
        """Add the top-10 slowest handlers (by p95) to ``health``.

        Args:
            health: the health dict being assembled.
        """
        if hasattr(self, "latency") and self.latency is not None:
            health["latency_top10"] = self.latency.get_top_n(n=10)

    def _collect_misc_metrics(self, health: dict) -> None:
        """Add replay-size + last runtime-probe report sections.

        Slight difference vs the handler-group equivalent:
        the runtime-probes section is added if the
        attribute exists at all (even if ``None``), since
        the mixin uses ``hasattr`` checks throughout.

        Args:
            health: the health dict being assembled.
        """
        if hasattr(self, "replay") and self.replay is not None:
            health["replay_size"] = self.replay.size()
        if hasattr(self, "runtime_probes"):
            health["runtime_probes"] = self.runtime_probes


def _log_probe(p: dict) -> None:
    """Emit one log line for a probe at its declared severity.

    Module-level helper used by
    ``ObservabilityRPCMixin.report_runtime_probes``.
    Unknown severities log at INFO rather than crash —
    defensive against frontend contracts evolving.

    Args:
        p: probe dict with at least ``name``, ``severity``,
            ``message`` keys.
    """
    name = p.get("name", "?")
    severity = p.get("severity", "?")
    message = p.get("message", "")
    line = f"[runtime-probe] {name}: {severity} — {message}"
    if severity == "error":
        logger.error(line)
    elif severity == "warning":
        logger.warning(line)
    else:
        logger.info(line)
