"""ObservabilityHandlers — diagnostics, metrics, runtime probes.

OP-25e | py_modules/unifideck/rpc/handlers/observability_handlers.py

Surfaces the plugin's internal observability data to the
frontend's diagnostics tab. Combines several sources:

* the plain bus's ``health`` snapshot (handler counts);
* the priority dispatcher's metrics (emitted / dispatched /
  coalesced / dropped);
* the watchdog's per-handler timeout state;
* the latency collector's top-N slowest handlers;
* the replay buffer's size + on-demand snapshot;
* the feature-flag service's current flag state;
* the probe-reaction service's recent history;
* an inbound endpoint where the frontend posts the result of
  its own runtime probes (browser-side capability checks).

This class differs from the others: it has **class-level
mutable attributes** (``dispatcher``, ``watchdog``, ``latency``,
``replay``, ``runtime_probes``) that are set after construction
via ``set_bus_collaborators`` — because the bus pipeline isn't
fully built when ``RpcHandlerBase.__init__`` runs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from unifideck.rpc.handlers.base import RpcHandlerBase
from unifideck.rpc.wrapper import RpcError

if TYPE_CHECKING:
    from unifideck.event_bus.priority_dispatcher import PriorityDispatcher
    from unifideck.event_bus.supervision.watchdog_handler import HandlerWatchdog

logger = logging.getLogger(__name__)


class ObservabilityHandlers(RpcHandlerBase):
    """Diagnostics + metrics RPC surface."""

    dispatcher: PriorityDispatcher | None = None
    watchdog: HandlerWatchdog | None = None
    latency: Any = None
    replay: Any = None
    runtime_probes: list[dict] | None = None

    def set_bus_collaborators(
        self,
        *,
        dispatcher: PriorityDispatcher | None = None,
        watchdog: HandlerWatchdog | None = None,
        latency: Any = None,
        replay: Any = None,
    ) -> None:
        """Inject bus-pipeline collaborators after construction.

        Called by the plugin boot sequence once the bus
        pipeline is fully wired. Storing them on the instance
        rather than passing through the base constructor
        keeps ``RpcHandlerBase`` stable and avoids a circular
        dependency between the bus pipeline and the handler
        graph.

        Args:
            dispatcher: optional priority dispatcher.
            watchdog: optional handler watchdog.
            latency: optional latency collector.
            replay: optional replay buffer.
        """
        self.dispatcher = dispatcher
        self.watchdog = watchdog
        self.latency = latency
        self.replay = replay

    async def get_plugin_metrics(self) -> Any:
        """Return the plugin-wide metrics snapshot.

        Delegates to ``MetricsCollector.get_plugin_metrics``
        which aggregates per-store sync metrics, download
        counts, and launch counts.

        Returns:
            Metrics dict from the collector.
        """
        metrics = self._require(self._services.metrics, "metrics")
        return cast(dict, metrics.get_plugin_metrics())

    async def get_bus_health(self) -> Any:
        """Compose a unified bus-health snapshot across every collaborator.

        Workflow:

        1. Start with whatever the bus's own ``health()``
           returns (may be ``{}`` for stub buses).
        2. Layer in dispatcher metrics, watchdog state,
           latency top-N, replay size, and the last runtime-
           probe report — each section is optional and only
           appears if the collaborator was wired.

        Returns:
            Multi-section dict whose shape depends on which
            collaborators are present.
        """
        health: dict[str, Any] = {}
        health_fn = getattr(self._bus, "health", None)
        if callable(health_fn):
            health = health_fn()
        self._collect_dispatcher_metrics(health)
        self._collect_watchdog_metrics(health)
        self._collect_latency_metrics(health)
        self._collect_misc_metrics(health)
        return cast(dict, health)

    def _collect_dispatcher_metrics(self, health: dict) -> None:
        """Add a ``dispatcher`` section to ``health`` if dispatcher is wired.

        Pulls a fresh ``DispatcherMetrics`` snapshot (which
        recomputes the live pending counts) and flattens the
        five counter fields into a dict on ``health``.

        Args:
            health: the health dict being assembled (mutated
                in place).
        """
        if self.dispatcher is None:
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

        Builds ``{handler_name → {invocations, timeouts,
        consecutive_timeouts, quarantined}}`` so the
        frontend can render a per-handler table.

        Args:
            health: the health dict being assembled.
        """
        if self.watchdog is None:
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
        if self.latency is not None:
            health["latency_top10"] = self.latency.get_top_n(n=10)

    def _collect_misc_metrics(self, health: dict) -> None:
        """Add replay-size + last runtime-probe report sections.

        Two unrelated mini-sections grouped here to keep the
        main collector readable.

        Args:
            health: the health dict being assembled.
        """
        if self.replay is not None:
            health["replay_size"] = self.replay.size()
        if self.runtime_probes is not None:
            health["runtime_probes"] = self.runtime_probes

    async def subscribe_replay(self, events: list) -> Any:
        """Return a snapshot of the replay buffer filtered to ``events``.

        Despite the "subscribe" name, this is a one-shot
        snapshot read (not a live subscription) — the
        frontend polls this periodically when the
        diagnostics panel is open.

        Args:
            events: list of event types to include
                (empty/None semantics handled by the
                replay buffer).

        Returns:
            List of replay entry dicts, or empty list if no
            replay buffer is wired.
        """
        if self.replay is None:
            return []
        return cast(list, self.replay.snapshot(events=events))

    async def release_quarantine(self, handler_name: str) -> Any:
        """Release a quarantined handler from the watchdog.

        Used by the "release handler" button on the
        diagnostics panel. Returns the watchdog's outcome
        (``True`` if a quarantine was actually cleared,
        ``False`` if the handler wasn't quarantined or
        doesn't exist).

        Args:
            handler_name: handler identifier (typically
                ``ClassName.method_name``).

        Returns:
            ``{success: bool, error?: str}`` — ``error`` is
            only set when the watchdog itself is missing.
        """
        if self.watchdog is None:
            return {"success": False, "error": "watchdog not wired"}
        released = self.watchdog.release_quarantine(handler_name)
        return {"success": released}

    async def get_feature_flags(self) -> Any:
        """Return the current feature-flag state.

        Delegates to ``FeatureFlagService.get_flags`` which
        returns ``{feature_name → bool}``.

        Returns:
            Feature-flag dict.

        Raises:
            RpcError: ``service_unavailable`` if the
                feature-flag service isn't wired.
        """
        if self._services.feature_flags is None:
            raise RpcError(
                "service_unavailable",
                service="feature_flags",
            )
        return cast(dict, self._services.feature_flags.get_flags())

    async def get_probe_history(self) -> Any:
        """Return the rolling history of past runtime-probe reports.

        Used by the diagnostics panel's "probe history"
        view to show what failed when. Delegates to
        ``ProbeReactionService.get_history``.

        Returns:
            List of probe-history entries (newest last).

        Raises:
            RpcError: ``service_unavailable`` if the
                probe-reaction service isn't wired.
        """
        if self._services.probe_reaction is None:
            raise RpcError(
                "service_unavailable",
                service="probe_reaction",
            )
        return cast(list, self._services.probe_reaction.get_history())

    async def report_runtime_probes(self, probes: list) -> Any:
        """Inbound: frontend posts the result of its own probe run.

        The frontend runs a periodic set of capability
        probes (SteamClient JS bridge reachable? router
        hook patched? etc.) and reports the result here.

        Workflow:

        1. Validate the input shape (must be a list).
        2. Cache the report on
           ``self.runtime_probes`` so
           ``_collect_misc_metrics`` can include it in
           ``get_bus_health``.
        3. Log every probe at its appropriate severity.
        4. Emit ``RUNTIME_PROBES_REPORTED`` on the bus so
           ``FeatureFlagService`` and
           ``ProbeReactionService`` can react.

        Args:
            probes: list of probe dicts
                ``{name, severity, message, ...}``.

        Returns:
            ``{ok, count, has_errors}`` summary for the
            frontend.
        """
        if not isinstance(probes, list):
            return {"ok": False, "error": "probes must be a list"}
        self.runtime_probes = probes
        self._log_probe_severities(probes)
        await self._emit_probes_event(probes)
        has_errors = any(p.get("severity") == "error" for p in probes)
        return {
            "ok": True,
            "count": len(probes),
            "has_errors": has_errors,
        }

    def _log_probe_severities(self, probes: list) -> None:
        """Emit one log line per probe at its declared severity.

        Maps the probe's ``severity`` field to the matching
        Python log level. Unknown severities log at INFO
        rather than crash — defensive against frontend
        contracts evolving.

        Args:
            probes: same list passed to
                ``report_runtime_probes``.
        """
        for p in probes:
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

    async def _emit_probes_event(self, probes: list) -> None:
        """Emit ``RUNTIME_PROBES_REPORTED`` on the bus, swallowing errors.

        Bus-emit failures (rare: no running loop, broken
        subscriber raising during the gather) are logged at
        DEBUG and absorbed — the RPC caller has already
        gotten its response, no point bubbling the error.

        Args:
            probes: same list passed to
                ``report_runtime_probes``.
        """
        try:
            from unifideck.core.types import Events

            await self._bus.emit(
                Events.RUNTIME_PROBES_REPORTED,
                probes=probes,
            )
        except Exception:
            logger.debug("[runtime-probe] bus emit failed")
