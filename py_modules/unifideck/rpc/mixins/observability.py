"""Observability RPC mixin for Plugin class.

OP-26a | rpc/mixins/observability.py
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.core.types.events import Events
from unifideck.rpc.errors import RpcError

logger = logging.getLogger(__name__)

_SEVERITY_LOG = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
}


class ObservabilityRPCMixin:
    """Metrics, bus health, replay, quarantine, feature flags, and probes."""

    bus: Any
    services: Any
    dispatcher: Any
    watchdog: Any
    latency: Any
    replay: Any
    runtime_probes: list[dict[str, Any]] | None = None

    def set_bus_collaborators(
        self,
        *,
        dispatcher: Any,
        watchdog: Any,
        latency: Any,
        replay: Any,
    ) -> None:
        """Inject optional EventBus pipeline collaborators."""
        self.dispatcher = dispatcher
        self.watchdog = watchdog
        self.latency = latency
        self.replay = replay

    async def get_plugin_metrics(self) -> Any:
        """Return MetricsCollector snapshot.

        Real method is ``get_plugin_metrics`` (see RPC handler
        twin for the full rationale).
        """
        metrics = getattr(self.services, "metrics", None)
        if metrics is None:
            raise RpcError("service_unavailable", service="metrics")
        return metrics.get_plugin_metrics()

    async def get_bus_health(self) -> Any:
        """Aggregate full EventBus + collaborator health.

        Mirror of the handler-class twin — :class:`EventBus` has
        no ``health()`` method, so we build the snapshot from
        ``_handlers`` and the pipeline collaborators' real APIs
        (``get_metrics``, ``get_snapshot``, …).
        """
        bus_handlers: dict[str, int] = {}
        for event_key, handlers in getattr(self.bus, "_handlers", {}).items():
            bus_handlers[event_key] = len(handlers)

        health: dict[str, Any] = {
            "bus": {
                "events_registered": len(bus_handlers),
                "handler_counts": bus_handlers,
            },
        }

        dispatcher = getattr(self, "dispatcher", None)
        if dispatcher is not None:
            m = dispatcher.get_metrics()
            health["dispatcher"] = getattr(m, "__dict__", m)

        watchdog = getattr(self, "watchdog", None)
        if watchdog is not None:
            raw = watchdog.get_metrics()
            health["watchdog"] = {
                name: getattr(m, "__dict__", m) for name, m in raw.items()
            }

        latency = getattr(self, "latency", None)
        if latency is not None:
            health["latency"] = latency.get_snapshot()

        probe_reaction = getattr(self.services, "probe_reaction", None)
        if probe_reaction is not None and hasattr(probe_reaction, "get_history"):
            health["probe_reaction"] = probe_reaction.get_history()
        return health

    async def subscribe_replay(self, events: list[str]) -> Any:
        """Return recent events for a frontend reconnect.

        Real method is ``EventReplayBuffer.snapshot(events=...)``
        — see handler twin for the rationale.
        """
        if getattr(self, "replay", None) is None:
            raise RpcError("service_unavailable", service="replay")
        # Pull in any toasts emitted by the (separate-process) game
        # launcher before snapshotting, so launcher-stage events reach
        # the frontend through this same poll. See launcher.frontend_bridge.
        self._drain_launcher_events()
        return self.replay.snapshot(events=events)

    def _drain_launcher_events(self) -> None:
        """Drain the launcher→plugin bridge file into the replay buffer."""
        drainer = getattr(self, "_launcher_drainer", None)
        if drainer is None:
            from unifideck.launcher.frontend_bridge import LauncherEventDrainer

            drainer = LauncherEventDrainer()
            self._launcher_drainer = drainer
        try:
            drainer.drain(self.replay)
        except Exception:
            logger.debug("[Observability] launcher event drain failed", exc_info=True)

    async def release_quarantine(self, handler_name: str) -> Any:
        """Release a watchdog-quarantined handler after a fix.

        Real method is ``HandlerWatchdog.release_quarantine`` —
        see handler twin for the rationale.
        """
        if getattr(self, "watchdog", None) is None:
            raise RpcError("service_unavailable", service="watchdog")
        return self.watchdog.release_quarantine(handler_name)

    async def get_feature_flags(self) -> Any:
        """Return current feature flag state.

        :class:`FeatureFlagService` exposes :meth:`get_flags` —
        an earlier version called ``get_all`` which doesn't exist.
        """
        flags = getattr(self.services, "feature_flags", None)
        if flags is None:
            return {}
        return flags.get_flags()

    async def get_probe_history(self) -> Any:
        """Return recent probe-reaction history."""
        return getattr(self, "runtime_probes", None) or []

    async def report_runtime_probes(self, probes: list[dict[str, Any]]) -> Any:
        """Store frontend boot-time CEF probe results."""
        if not isinstance(probes, list):
            raise RpcError("invalid_input", detail="probes must be a list")
        for probe in probes:
            severity = probe.get("severity", "info")
            level = _SEVERITY_LOG.get(severity, logging.INFO)
            logger.log(level, "Runtime probe: %s", probe.get("name", "unknown"))
        self.runtime_probes = probes
        await self.bus.emit(Events.RUNTIME_PROBES_REPORTED, probes=probes)
        has_errors = any(p.get("severity") == "error" for p in probes)
        return {"ok": True, "count": len(probes), "has_errors": has_errors}
