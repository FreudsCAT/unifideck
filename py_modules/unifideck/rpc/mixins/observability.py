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
        self.dispatcher = dispatcher
        self.watchdog = watchdog
        self.latency = latency
        self.replay = replay

    async def get_plugin_metrics(self) -> Any:
        metrics = getattr(self.services, "metrics", None)
        if metrics is None:
            raise RpcError("service_unavailable", service="metrics")
        return metrics.collect()

    async def get_bus_health(self) -> Any:
        health: dict[str, Any] = self.bus.health()
        if getattr(self, "dispatcher", None) is not None:
            health["dispatcher"] = self.dispatcher.stats()
        if getattr(self, "watchdog", None) is not None:
            health["watchdog"] = self.watchdog.stats()
        if getattr(self, "latency", None) is not None:
            health["latency"] = self.latency.stats()
        probe_reaction = getattr(self.services, "probe_reaction", None)
        if probe_reaction is not None:
            health["probe_reaction"] = probe_reaction.stats()
        return health

    async def subscribe_replay(self, events: list[str]) -> Any:
        if getattr(self, "replay", None) is None:
            raise RpcError("service_unavailable", service="replay")
        return self.replay.subscribe(events)

    async def release_quarantine(self, handler_name: str) -> Any:
        if getattr(self, "watchdog", None) is None:
            raise RpcError("service_unavailable", service="watchdog")
        return self.watchdog.release(handler_name)

    async def get_feature_flags(self) -> Any:
        flags = getattr(self.services, "feature_flags", None)
        if flags is None:
            return {}
        return flags.get_all()

    async def get_probe_history(self) -> Any:
        return getattr(self, "runtime_probes", None) or []

    async def report_runtime_probes(self, probes: list[dict[str, Any]]) -> Any:
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
