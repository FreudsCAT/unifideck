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
        return self.replay.snapshot(events=events)

    async def get_launcher_toasts(self) -> Any:
        """Return launcher-subprocess toasts written since the last poll.

        The game launcher is a separate process; it appends
        LAUNCHER_STAGE toasts to a shared file
        (``launcher.frontend_bridge``) that this RPC drains. A
        *persistent* frontend poll calls it regardless of whether the
        QAM panel is open, so launch-time toasts (first-time prefix
        setup, dependency install, Proton switch, …) appear in Gaming
        Mode. Returns a list of payloads
        ``{i18n_key, i18n_title_key?, i18n_params?, severity?, action?}``.
        """
        drainer = getattr(self, "_launcher_drainer", None)
        if drainer is None:
            from unifideck.launcher.frontend_bridge import LauncherEventDrainer

            drainer = LauncherEventDrainer()
            self._launcher_drainer = drainer
        try:
            return drainer.poll_new()
        except Exception:
            logger.debug("[Observability] launcher toast poll failed", exc_info=True)
            return []

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

    async def capture_logs(self, dest_path: str = "") -> Any:
        """Collect every log + diagnostic into one zip in Downloads.

        Exists to end the "where are your logs" round-trip: the
        artifacts live in four unrelated places whose paths differ per
        user, distro and Steam layout, so asking a reporter to find
        them by hand reliably produces the wrong subset.

        ``dest_path`` is normally empty, which means "use
        ``logs.export_path`` and then the usual fallbacks". A caller may
        pass a directory or a full ``.zip`` path to override it.

        The returned dict describes the archive (path, size, file
        count) plus what was skipped and which sanity checks failed.
        Every string in it is a stable machine code, never prose, so
        the frontend maps them through i18n.
        """
        svc = getattr(self.services, "support_bundle", None)
        if svc is None:
            raise RpcError("service_unavailable", service="support_bundle")
        try:
            return await svc.capture(dest_path, extra=self._support_bundle_extra())
        except RpcError:
            raise
        except OSError as err:
            # The one expected failure: nothing writable to put it in.
            logger.warning("[Observability] log capture destination failed: %s", err)
            raise RpcError("bundle_dest_unwritable", detail=str(err)) from err
        except Exception as err:
            logger.exception("[Observability] log capture failed")
            raise RpcError("bundle_failed", detail=repr(err)) from err

    def _support_bundle_extra(self) -> dict[str, Any]:
        """Gather the facts only this layer can see.

        Feature flags and the frontend's boot-time CEF probe results
        live on the plugin instance, not on the filesystem, so the
        collector cannot reach them. Kept sync and underscore-prefixed
        so the RPC auto-wrapper skips it. Each lookup is guarded
        individually — a missing flag service must not cost us the
        whole bundle.
        """
        extra: dict[str, Any] = {}
        flags = getattr(self.services, "feature_flags", None)
        if flags is not None:
            try:
                extra["feature_flags"] = flags.get_flags()
            except Exception:
                logger.debug("[Observability] flag snapshot failed", exc_info=True)
        probes = getattr(self, "runtime_probes", None)
        if probes:
            extra["runtime_probes"] = probes
        return extra

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
