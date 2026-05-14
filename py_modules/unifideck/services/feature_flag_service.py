"""services/feature_flag_service.py — Probe-driven feature flags.

Listens for ``RUNTIME_PROBES_REPORTED`` after the frontend's
boot-time CEF probe suite completes. Translates failing probes
into disabled features via ``PROBE_TO_FEATURES``, exposes flags
via RPC so hooks consult them before using a capability.
State is in-memory only — resets at every plugin reload. Probes
re-run at each boot so flags stay fresh with the current Steam
client state.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

# Probe id → features it gates. One failing probe disables every
# feature in its list. Kept as the module-level default; user
# config at ``probes.probe_to_features`` can override per probe id.
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

# Every feature we know about. Starts enabled; probes may disable.
ALL_FEATURES: list[str] = sorted({
    f for features in PROBE_TO_FEATURES.values() for f in features
})


class FeatureFlagService:
    """Reactive feature flag store driven by runtime probes."""

    def __init__(
        self, bus: EventBus, config: object | None = None,
    ) -> None:
        """Merge config-supplied mapping, init flags."""
        self._bus = bus
        self._mapping = self._load_mapping(config)

        self._flags = dict.fromkeys(ALL_FEATURES, True)

        # ``auto_wire(self, bus)`` walks ``self``'s methods
        # and registers every ``@subscribe(Events.X)``-marked
        # handler with the bus. Earlier this site called
        # ``self._bus.auto_wire(self)`` guarded by
        # ``hasattr`` — but ``auto_wire`` is module-level,
        # not a bus method, so the hasattr check returned
        # False and every subscription was silently dropped.
        auto_wire(self, self._bus)

    @staticmethod
    def _load_mapping(config: object | None) -> dict[str, list[str]]:
        """Return the probe→features mapping."""
        mapping = PROBE_TO_FEATURES.copy()

        # Early-return guards flatten the 5-level pyramid into a
        # single linear pass — same structural fix as
        # :meth:`ProbeReactionService._load_mapping`.
        if config is None or not hasattr(config, "get"):
            return mapping
        try:
            user_mapping = config.get("probes.probe_to_features")  # type: ignore[attr-defined]
        except Exception as e:
            # User overrides for probes.probe_to_features may be
            # malformed or missing; fall back to defaults.
            logger.debug("[FeatureFlags] user probe-mapping load failed: %s", e)
            return mapping
        if not isinstance(user_mapping, dict):
            return mapping
        for k, v in user_mapping.items():
            if isinstance(v, list) and all(isinstance(i, str) for i in v):
                mapping[k] = v
        return mapping

    def get_flags(self) -> dict[str, bool]:
        """Return a copy of the current feature flag state."""
        return self._flags.copy()

    def is_enabled(self, feature: str) -> bool:
        """Check one flag. Unknown features return True."""
        return self._flags.get(feature, True)

    @subscribe(Events.RUNTIME_PROBES_REPORTED)
    async def _on_probes_reported(self, **kwargs: Any) -> None:
        """Update flags from the report."""
        probes = kwargs.get("probes")
        if not isinstance(probes, list):
            return

        changed = self._apply_probes_to_flags(probes)
        if changed:
            logger.info("[FeatureFlagService] Flags changed: %s", changed)

    def _apply_probes_to_flags(self, probes: list[dict[str, Any]]) -> list[str]:
        """Walk probes and update affected features."""
        changed = []

        for probe in probes:
            # Handle both formats
            probe_id = probe.get("id") or probe.get("name")
            if not probe_id or probe_id not in self._mapping:
                continue

            verdict = probe.get("verdict") or probe.get("severity")
            if not verdict:
                continue

            verdict = str(verdict).lower()

            # Disable on fail/error, enable on ok/info
            features_affected = self._mapping[probe_id]

            if verdict in ("fail", "error"):
                new_state = False
            elif verdict in ("ok", "info"):
                new_state = True
            else:
                continue  # 'warn' leaves untouched

            for feature in features_affected:
                # Add to unknown features dynamically if missing
                if feature not in self._flags:
                    self._flags[feature] = True

                if self._flags[feature] != new_state:
                    self._flags[feature] = new_state
                    changed.append(f"{feature}={new_state}")

        return changed
