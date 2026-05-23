"""Shortcut migration helpers — one-shot fixers for legacy state.

Currently houses:

* :func:`audit_appid_drift` — scans the shortcuts registry and
  reports entries whose stored appid no longer matches
  ``generate_app_id(launcher, title)``. Drift happens when the
  launcher path changes (e.g. plugin moved between Decky / Steam
  prefixes) or when a very old version used a different hash.

Migrations are **never auto-run**. Each helper returns a report;
the caller (admin RPC, manual script) decides whether to apply
fixes. Auto-mutating user state on plugin update is too risky —
better to surface the drift in logs and let the user opt in.
"""
from __future__ import annotations

import logging
from typing import Any

from .games_map import generate_app_id
from .registry import load_registry

logger = logging.getLogger(__name__)


def audit_appid_drift(launcher_path: str) -> list[dict[str, Any]]:
    """Scan the registry for shortcuts whose appid disagrees with the hash.

    Reads ``shortcuts_registry.json`` and computes the expected
    appid for each entry from ``(launcher_path, title)``. Returns
    a list of drift records:

        [{"store_id": "...", "title": "...",
          "registered_appid": int, "expected_appid": int}, ...]

    Empty list means no drift — registry is in sync with the current
    launcher path. Logged at WARNING when drift is found so the
    issue surfaces in tailed logs without needing a separate RPC.

    Args:
        launcher_path: absolute path the launcher binary the plugin
            currently uses. Drift typically reflects a launcher-path
            change since the entries were created.
    """
    if not launcher_path:
        return []
    registry = load_registry()
    drift: list[dict[str, Any]] = []
    for store_id, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        title = entry.get("title") or ""
        registered = entry.get("appid_unsigned") or entry.get("appid")
        if not isinstance(title, str) or registered is None:
            continue
        try:
            expected = generate_app_id(launcher_path, title)
        except Exception:
            logger.debug(
                "[ShortcutService.migrations] generate_app_id failed for %s",
                store_id,
                exc_info=True,
            )
            continue
        # The registry may store the unsigned form; normalise both
        # sides to signed-int (the canonical Steam shape) before
        # comparing so we don't false-positive on sign-extension.
        registered_signed = _to_signed_int32(int(registered))
        if registered_signed != expected:
            drift.append({
                "store_id": store_id,
                "title": title,
                "registered_appid": registered_signed,
                "expected_appid": expected,
            })
    if drift:
        logger.warning(
            "[ShortcutService.migrations] appid drift detected — "
            "%d entries differ from the hash of (launcher_path, title). "
            "Run the admin migration RPC to re-key them.",
            len(drift),
        )
    return drift


def _to_signed_int32(value: int) -> int:
    """Coerce a possibly-unsigned 32-bit int into signed int32 range."""
    value &= 0xFFFFFFFF
    if value > 0x7FFFFFFF:
        value -= 0x100000000
    return value
