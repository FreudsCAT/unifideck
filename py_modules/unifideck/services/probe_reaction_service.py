"""Probe-reaction service — store-side reactions to subscription probes.

OP-12e | py_modules/unifideck/services/probe_reaction_service.py

When a subscription probe fires (e.g. "Game Pass subscription expired"
from ``microsoft_subscription``), some stores need to react locally
without the user pressing a button : pause downloads tied to the
expired subscription, hide games that became unplayable, etc.

``ProbeReactionService`` is the dispatcher that listens for these
probe events and routes them to per-store handler functions registered
at construction time. Decouples the probe-emission side
(``microsoft_subscription/probe_emission.py``) from the store-side
reactions.
"""

from __future__ import annotations
import logging
import time
from collections import deque
from typing import Any
from ..core.types import Events
from ..event_bus.event_bus import EventBus
from ..event_bus.event_bus_devex import auto_wire, subscribe

logger = logging.getLogger(__name__)
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
    """Probe reaction service."""

    def __init__(
        self,
        bus: EventBus,
        watchdog: Any,
        config: object | None = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._watchdog = watchdog
        self._mapping = self._load_mapping(config)
        self._history: deque[dict] = deque(maxlen=HISTORY_MAX_ENTRIES)
        auto_wire(self, self._bus, watchdog=watchdog)
        logger.info(
            "[ProbeReactionService] initialized with %d probe mappings",
            len(self._mapping),
        )

    @staticmethod
    def _load_mapping(config: object | None) -> dict[str, list[str]]:
        """Load mapping."""
        if config is None or not hasattr(config, "get"):
            return dict(PROBE_TO_HANDLERS)
        raw = config.get("probes.probe_to_handlers")
        if not isinstance(raw, dict):
            return dict(PROBE_TO_HANDLERS)
        merged = dict(PROBE_TO_HANDLERS)
        for k, v in raw.items():
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                merged[k] = v
        return merged

    def get_history(self) -> list[dict]:
        """Get history."""
        return list(self._history)

    @subscribe(Events.RUNTIME_PROBES_REPORTED)
    async def _on_probes_reported(self, **kwargs) -> None:
        """On probes reported."""
        probes = kwargs.get("probes")
        if not isinstance(probes, list):
            return
        self._record_in_history(probes)
        self._quarantine_affected_handlers(probes)

    def _record_in_history(self, probes: list) -> None:
        """Record in history."""
        entry = {
            "timestamp": time.monotonic(),
            "probes": [
                {
                    "id": p.get("id") or p.get("name", "?"),
                    "verdict": (
                        p.get("verdict")
                        or ("fail" if p.get("severity") == "error" else "ok")
                    ),
                }
                for p in probes
                if isinstance(p, dict)
            ],
        }
        self._history.append(entry)

    def _quarantine_affected_handlers(self, probes: list) -> None:
        """Quarantine affected handlers."""
        if self._watchdog is None:
            return
        affected: dict[str, str] = {}
        for probe in probes:
            if not isinstance(probe, dict):
                continue
            is_fail = probe.get("verdict") == "fail" or probe.get("severity") == "error"
            if not is_fail:
                continue
            probe_id = probe.get("id") or probe.get("name")
            if not isinstance(probe_id, str):
                continue
            for handler_name in self._mapping.get(probe_id, []):
                affected.setdefault(handler_name, probe_id)
        quarantined: list[str] = []
        for handler_name, probe_id in affected.items():
            if self._watchdog.quarantine_preemptive(
                handler_name,
                reason=f"probe:{probe_id}",
            ):
                quarantined.append(handler_name)
        if quarantined:
            logger.warning(
                "[ProbeReactionService] quarantined %d handlers: %s",
                len(quarantined),
                quarantined,
            )
