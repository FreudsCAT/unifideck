"""Feature-flag service — probe-driven runtime feature toggles.

OP-12d | py_modules/unifideck/services/feature_flag_service.py

``FeatureFlagService`` automatically enables or disables features
based on the verdicts of runtime probes. A probe is a small
diagnostic that checks one capability of the Steam Deck environment
(``steam_client_apps`` checks that the SteamClient.Apps JS API is
reachable, ``router_hook_patch`` checks that Decky's router patching
took effect, etc.).

The ``PROBE_TO_FEATURES`` table declares which features depend on
which probe — when ``steam_client_apps`` fails, shortcut creation,
artwork injection and play-button override are all auto-disabled
because none of them can work without that API. When the probe
passes again on the next report, the features are auto-re-enabled.

This is the inverse of a kill-switch: features default to **on** at
construction and only flip off if a probe explicitly reports a
failure. Listening services check ``is_enabled("shortcut_creation")``
before performing any work that needs the underlying capability,
and gracefully degrade when it returns ``False``.
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
    """Toggle features on/off based on runtime probe verdicts."""

    def __init__(self, bus: EventBus, config: object | None = None) -> None:
        """Initialise flags to True and subscribe to probe events.

        The probe → feature mapping is loaded from the config (with
        the bundled ``PROBE_TO_FEATURES`` as fallback). Every
        feature starts as enabled — they only flip off when a probe
        explicitly fails.

        Args:
            bus: live event bus on which the service subscribes to
                ``RUNTIME_PROBES_REPORTED``.
            config: optional config-like object exposing ``get()``.
                If provided and ``probes.probe_to_features`` is a
                well-shaped dict, it overrides the defaults.
        """
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
        """Build the probe-to-features map from config + defaults.

        Starts with the hard-coded ``PROBE_TO_FEATURES`` and merges
        in any well-shaped overrides from ``probes.probe_to_features``
        in the config. Each value must be a list of strings to be
        accepted (defensive: a typo in the user config shouldn't
        crash the service).

        Args:
            config: optional config-like object exposing ``get()``.

        Returns:
            The merged mapping (defaults + valid overrides).
        """
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
        """Return a snapshot copy of the current flag state.

        Returns:
            ``feature_name → bool`` mapping. Mutating the returned
            dict has no effect on the service (it's a fresh copy).
        """
        return dict(self._flags)

    def is_enabled(self, feature: str) -> bool:
        """Return whether the named feature is currently enabled.

        Unknown feature names default to ``True`` — the service
        deliberately doesn't gate on its own knowledge of feature
        names so a caller asking about a feature that was added
        after the service started is treated as "yes, that works".

        Args:
            feature: feature name (e.g. ``"shortcut_creation"``).

        Returns:
            ``True`` if the feature is enabled (or unknown to the
            service), ``False`` if a probe has explicitly disabled
            it.
        """
        return self._flags.get(feature, True)

    @subscribe(Events.RUNTIME_PROBES_REPORTED)
    async def _on_probes_reported(self, **kwargs) -> None:
        """React to a ``RUNTIME_PROBES_REPORTED`` event.

        Stores the latest report (for diagnostics readout) and
        applies the new probe verdicts to the flag state through
        ``_apply_probes_to_flags``. Logs at INFO when any feature
        actually changed state, silently otherwise.
        """
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
        """Walk probes and update flags for the affected features.

        For each probe with a clear verdict (``"ok"`` or ``"fail"``,
        or equivalent severity), looks up the affected features in
        the mapping and flips them. Returns the list of features
        whose state actually changed (a probe re-confirming a
        verdict doesn't produce a change).

        Probes with ambiguous verdicts (no ``verdict`` / ``severity``
        field, or a ``"warn"`` severity) are silently skipped —
        partial reports are tolerated.

        Args:
            probes: list of probe dicts as emitted by the runtime
                probe runner.

        Returns:
            List of feature names whose enabled state was changed
            by this call.
        """
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
