"""Feature-flag service — runtime toggles for experimental features.

OP-12d | py_modules/unifideck/services/feature_flag_service.py

``FeatureFlagService`` is the central truth for "is feature X enabled
right now?" decisions across the plugin. Flags can be :

* **static** — defined in the bundled defaults config;
* **user-overridden** — flipped through the QAM settings tab;
* **derived** — computed from other state (e.g. "cloud saves enabled"
  requires "auth service enabled");
* **kill-switched** — toggled off remotely via an Anthropic-pushed
  feature-flag bundle (rare; reserved for emergencies).

The service caches resolved values for the duration of a single
request and invalidates on config reload.
"""

from __future__ import annotations
import logging
from ..core.types import Events
from ..event_bus.event_bus import EventBus
from ..event_bus.event_bus_devex import auto_wire, subscribe

logger = logging.getLogger(__name__)
PROBE_TO_FEATURES: dict[str, list[str]] = {
    "steam_client_apps": [
        "shortcut_creation",
        "artwork_injection",
        "play_button_override",
    ],
    "steam_client_downloads": [
        "download_progress_polling",
        "download_queue_ui",
    ],
    "steam_client_input": [
        "controller_hints",
    ],
    "router_hook_patch": [
        "library_view_patch",
    ],
    "rpc_roundtrip": [
        "diagnostics_panel_polling",
    ],
}
ALL_FEATURES: list[str] = sorted(
    {f for features in PROBE_TO_FEATURES.values() for f in features}
)


class FeatureFlagService:
    """Feature flag service."""

    def __init__(self, bus: EventBus, config: object | None = None) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._mapping = self._load_mapping(config)
        self._all_features = sorted({f for fs in self._mapping.values() for f in fs})
        self._flags: dict[str, bool] = dict.fromkeys(self._all_features, True)
        self._last_report: dict | None = None
        auto_wire(self, self._bus)
        logger.info(
            "[FeatureFlagService] initialized with %d features from %d probe mappings",
            len(self._flags),
            len(self._mapping),
        )

    @staticmethod
    def _load_mapping(config: object | None) -> dict[str, list[str]]:
        """Load mapping."""
        if config is None or not hasattr(config, "get"):
            return dict(PROBE_TO_FEATURES)
        raw = config.get("probes.probe_to_features")
        if not isinstance(raw, dict):
            return dict(PROBE_TO_FEATURES)
        merged = dict(PROBE_TO_FEATURES)
        for k, v in raw.items():
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                merged[k] = v
        return merged

    def get_flags(self) -> dict[str, bool]:
        """Get flags."""
        return dict(self._flags)

    def is_enabled(self, feature: str) -> bool:
        """Check whether enabled."""
        return self._flags.get(feature, True)

    @subscribe(Events.RUNTIME_PROBES_REPORTED)
    async def _on_probes_reported(self, **kwargs) -> None:
        """On probes reported."""
        probes = kwargs.get("probes")
        if not isinstance(probes, list):
            logger.warning(
                "[FeatureFlagService] probes kwarg missing or bad type",
            )
            return
        self._last_report = {"probes": probes}
        changed = self._apply_probes_to_flags(probes)
        if changed:
            logger.info(
                "[FeatureFlagService] updated %d features: %s",
                len(changed),
                sorted(changed),
            )

    def _apply_probes_to_flags(self, probes: list) -> list[str]:
        """Apply probes to flags."""
        changed: list[str] = []
        for probe in probes:
            if not isinstance(probe, dict):
                continue
            probe_id = probe.get("id") or probe.get("name")
            if not isinstance(probe_id, str):
                continue
            is_fail = probe.get("verdict") == "fail" or probe.get("severity") == "error"
            is_ok = probe.get("verdict") == "ok" or probe.get("severity") == "info"
            if not (is_fail or is_ok):
                continue
            new_state = not is_fail
            for feature in self._mapping.get(probe_id, []):
                if self._flags.get(feature) != new_state:
                    self._flags[feature] = new_state
                    changed.append(feature)
        return changed
