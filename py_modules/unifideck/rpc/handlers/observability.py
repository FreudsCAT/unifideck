"""Observability RPC handlers.

OP-25e | py_modules/unifideck/rpc/handlers/observability.py
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.core.types.events import Events
from unifideck.rpc.errors import RpcError
from unifideck.rpc.handlers.base import RpcHandlerBase

logger = logging.getLogger(__name__)

_SEVERITY_LOG = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
}


class ObservabilityHandlers(RpcHandlerBase):
    """Metrics, bus health, replay, quarantine, feature flags, and probes."""

    dispatcher: Any | None = None
    watchdog: Any | None = None
    latency: Any | None = None
    replay: Any | None = None
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
        metrics = self._require(
            getattr(self._services, "metrics", None), "metrics",
        )
        return metrics.collect()

    async def get_bus_health(self) -> Any:
        health: dict[str, Any] = self._bus.health()
        self._collect_dispatcher_metrics(health)
        self._collect_watchdog_metrics(health)
        self._collect_latency_metrics(health)
        self._collect_misc_metrics(health)
        return health

    async def subscribe_replay(self, events: list[str]) -> Any:
        if self.replay is None:
            raise RpcError("service_unavailable", service="replay")
        return self.replay.subscribe(events)

    async def release_quarantine(self, handler_name: str) -> Any:
        if self.watchdog is None:
            raise RpcError("service_unavailable", service="watchdog")
        return self.watchdog.release(handler_name)

    async def get_feature_flags(self) -> Any:
        flags = getattr(self._services, "feature_flags", None)
        if flags is None:
            return {}
        return flags.get_all()

    async def get_probe_history(self) -> Any:
        return self.runtime_probes or []

    async def report_runtime_probes(self, probes: list[dict[str, Any]]) -> Any:
        if not isinstance(probes, list):
            raise RpcError("invalid_input", detail="probes must be a list")
        self._log_probe_severities(probes)
        self.runtime_probes = probes
        await self._emit_probes_event(probes)
        has_errors = any(p.get("severity") == "error" for p in probes)
        return {"ok": True, "count": len(probes), "has_errors": has_errors}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _collect_dispatcher_metrics(self, health: dict[str, Any]) -> None:
        if self.dispatcher is None:
            return
        health["dispatcher"] = self.dispatcher.stats()

    def _collect_watchdog_metrics(self, health: dict[str, Any]) -> None:
        if self.watchdog is None:
            return
        health["watchdog"] = self.watchdog.stats()

    def _collect_latency_metrics(self, health: dict[str, Any]) -> None:
        if self.latency is None:
            return
        health["latency"] = self.latency.stats()

    def _collect_misc_metrics(self, health: dict[str, Any]) -> None:
        probe_reaction = getattr(self._services, "probe_reaction", None)
        if probe_reaction is not None:
            health["probe_reaction"] = probe_reaction.stats()

    def _log_probe_severities(self, probes: list[dict[str, Any]]) -> None:
        for probe in probes:
            severity = probe.get("severity", "info")
            level = _SEVERITY_LOG.get(severity, logging.INFO)
            logger.log(level, "Runtime probe: %s", probe.get("name", "unknown"))

    async def _emit_probes_event(self, probes: list[dict[str, Any]]) -> None:
        await self._bus.emit(
            Events.RUNTIME_PROBES_REPORTED, probes=probes,
        )
