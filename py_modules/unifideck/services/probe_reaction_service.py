"""Probe-reaction service — pre-emptively quarantine fragile bus handlers.

OP-12e | py_modules/unifideck/services/probe_reaction_service.py

When a runtime probe reports a failed verdict (e.g. the
``steam_client_apps`` probe detects that Steam's JS bridge is
unreachable), the bus handlers that depend on that capability are
guaranteed to throw the next time they're invoked. Rather than
waiting for the handler to fail and the watchdog to react, this
service **pre-emptively quarantines** the affected handlers on the
watchdog so the bus skips them entirely until the probe recovers.

The ``PROBE_TO_HANDLERS`` table declares which bus handlers depend
on which probe — when ``steam_client_apps`` fails, the artwork
service's shortcut-created handler and the shortcut service's
download/sync handlers are all quarantined together (they share the
same dependency on the SteamClient JS bridge).

A short rolling history of the last 50 probe reports is kept for
diagnostics (visible in the QAM debug panel).
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
    """Quarantine bus handlers preemptively when their probe fails."""

    def __init__(
        self,
        bus: EventBus,
        watchdog: Any,
        config: object | None = None,
    ) -> None:
        """Wire the service to the bus and watchdog.

        Loads the probe → handlers map (config-overridable, with
        the bundled ``PROBE_TO_HANDLERS`` as fallback), prepares the
        rolling history deque, and auto-wires
        ``_on_probes_reported`` to the bus.

        Args:
            bus: live event bus on which the service subscribes to
                ``RUNTIME_PROBES_REPORTED``.
            watchdog: the bus handler watchdog (typically
                ``BusPipeline.watchdog``). Its
                ``quarantine_preemptive`` method is called for each
                affected handler.
            config: optional config-like object exposing ``get()``.
                ``probes.probe_to_handlers`` overrides the bundled
                mapping when well-shaped.
        """
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
        """Merge bundled probe mappings with config overrides.

        Starts with the hard-coded ``PROBE_TO_HANDLERS`` and merges
        in any well-shaped overrides from
        ``probes.probe_to_handlers`` in the config. Each value must
        be a list of strings (handler dotted names) to be accepted;
        malformed entries are ignored.

        Args:
            config: optional config-like object exposing ``get()``.

        Returns:
            The merged mapping ready to be used as instance state.
        """
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
        """Return a snapshot of the last 50 probe reports.

        Returns:
            List of timestamped report entries in chronological
            order (oldest first). Each entry has a ``timestamp``
            and a list of ``{id, verdict}`` per probe. Mutating the
            returned list has no effect on the service.
        """
        return list(self._history)

    @subscribe(Events.RUNTIME_PROBES_REPORTED)
    async def _on_probes_reported(self, **kwargs) -> None:
        """Handle a ``RUNTIME_PROBES_REPORTED`` bus event.

        Records the report in history and pre-emptively quarantines
        every handler affected by a failing probe. Silently ignores
        malformed payloads (missing or non-list ``probes`` kwarg).
        """
        probes = kwargs.get("probes")
        if not isinstance(probes, list):
            return
        self._record_in_history(probes)
        self._quarantine_affected_handlers(probes)

    def _record_in_history(self, probes: list) -> None:
        """Append a slim summary of the probes to the history deque.

        Stores only ``id`` and ``verdict`` per probe (no full probe
        payload) to keep memory bounded — the history is intended
        for diagnostics, not for replaying probe data.

        Args:
            probes: list of probe dicts from the bus event payload.
        """
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
        """Walk failing probes and quarantine their affected handlers.

        For each probe with a clear ``fail`` verdict, looks up the
        affected handlers in ``self._mapping`` and calls
        ``watchdog.quarantine_preemptive(handler, reason)``. Handlers
        already quarantined return ``False`` from that method and
        are not double-counted in the log.

        Args:
            probes: list of probe dicts from the bus event payload.
        """
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
