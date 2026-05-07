"""services/probe_reaction_service.py — React to boot-time probe failures.

Two in-memory concerns sharing a single ``@subscribe`` handler:
1. Preemptive watchdog quarantine — when a probe fails, every
   handler listed in ``PROBE_TO_HANDLERS`` for that probe is
   quarantined BEFORE it gets a chance to fail at runtime.
   Avoids the usual 10-consecutive-timeout quarantine cascade.
2. Bounded in-session history — last 50 probe reports kept in
   a deque for DiagnosticsPanel. No disk persistence — keeps
   the service stateless across reloads.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from ..core.types.events import Events
from ..event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from ..event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

# Probe id → handlers to preemptively quarantine on failure.
# router_hook_patch + rpc_roundtrip are frontend-only — no
# backend handlers to quarantine for those.
PROBE_TO_HANDLERS: dict[str, list[str]] = {
    "steam_client_apps": [
        "ArtworkService._on_shortcut_created",
        "ShortcutService._on_download_complete",
        "ShortcutService._on_sync_complete",
    ],
    "steam_client_downloads": [
        "ShortcutService._on_download_complete",
    ],
}

HISTORY_MAX_ENTRIES = 50


class ProbeReactionService:
    """React to probe reports: quarantine handlers, keep history."""

    def __init__(
        self,
        bus: EventBus,
        watchdog: Any,
        config: object | None = None,
    ) -> None:
        """Store refs, init history deque, auto_wire."""
        self._bus = bus
        self._watchdog = watchdog
        self._mapping = self._load_mapping(config)
        self._history: deque[dict[str, Any]] = deque(maxlen=HISTORY_MAX_ENTRIES)
        
        if hasattr(self._bus, "auto_wire"):
            self._bus.auto_wire(self)

    @staticmethod
    def _load_mapping(config: object | None) -> dict[str, list[str]]:
        """Merge user config at ``probes.probe_to_handlers`` with defaults."""
        mapping = PROBE_TO_HANDLERS.copy()
        
        if config and hasattr(config, "get"):
            try:
                user_mapping = config.get("probes.probe_to_handlers") # type: ignore
                if isinstance(user_mapping, dict):
                    for k, v in user_mapping.items():
                        if isinstance(v, list) and all(isinstance(i, str) for i in v):
                            mapping[k] = v
            except Exception:
                pass
                
        return mapping

    def get_history(self) -> list[dict[str, Any]]:
        """Return a snapshot of the in-session probe history."""
        return list(self._history)

    @subscribe(Events.RUNTIME_PROBES_REPORTED)
    async def _on_probes_reported(self, **kwargs: Any) -> None:
        """Record the report in history + quarantine affected handlers."""
        probes = kwargs.get("probes")
        if not isinstance(probes, list):
            return
            
        self._record_in_history(probes)
        self._quarantine_affected_handlers(probes)

    def _record_in_history(self, probes: list[dict[str, Any]]) -> None:
        import time
        self._history.append({
            "timestamp": time.time(),
            "probes": probes
        })

    def _quarantine_affected_handlers(self, probes: list[dict[str, Any]]) -> None:
        if not self._watchdog or not hasattr(self._watchdog, "force_quarantine"):
            return
            
        for probe in probes:
            probe_id = probe.get("id") or probe.get("name")
            if not probe_id or probe_id not in self._mapping:
                continue
                
            verdict = probe.get("verdict") or probe.get("severity")
            if not verdict:
                continue
                
            verdict = str(verdict).lower()
            
            if verdict in ("fail", "error"):
                handlers = self._mapping[probe_id]
                for handler_name in handlers:
                    logger.info(
                        "[ProbeReaction] Preemptively quarantining %s due to %s failure",
                        handler_name, probe_id
                    )
                    self._watchdog.force_quarantine(handler_name, reason=f"{probe_id} probe failed")
