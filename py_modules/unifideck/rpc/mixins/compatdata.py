"""
CompatdataRPCMixin — report and reclaim stale Steam ``compatdata`` prefixes.

py_modules/unifideck/rpc/mixins/compatdata.py

* ``scan_stale_compatdata``   — classified inventory + reclaimable size
* ``delete_stale_compatdata`` — delete only the entries the scan marked
  deletable

Background in ``services/shortcut/compatdata_scan``: Steam-created prefixes
for Unifideck shortcuts are dead weight (the launcher points ``WINEPREFIX``
elsewhere) and can reach hundreds of MB each, but the same appid range also
holds prefixes for the user's *own* non-Steam shortcuts, which are live.

The deletion RPC therefore **re-runs the scan** and intersects the caller's
appid list with the freshly-computed deletable set. A stale frontend list, a
tampered payload, or a shortcut added between scan and confirm can then never
delete a user-owned prefix — the guarantee does not depend on the client.

Lives in its own mixin rather than in ``sync_cleanup`` to keep both files
under the volumetry cap.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from unifideck.core.safe_delete import safe_rmtree
from unifideck.services.shortcut import compatdata_scan

logger = logging.getLogger(__name__)


class CompatdataRPCMixin:
    """Stale ``compatdata`` inventory + reclaim."""

    services: Any

    async def _compatdata_inputs(self) -> tuple[Any, dict[str, Any]]:
        """``(steam_root, shortcuts)`` for a scan — both may be empty."""
        from unifideck.utils.vdf_compat import resolve_live_steam_root

        shortcut_svc = getattr(self.services, "shortcut", None)
        shortcuts: dict[str, Any] = {}
        if shortcut_svc is not None:
            await shortcut_svc._load_shortcuts()
            raw = getattr(shortcut_svc, "_shortcuts", {}) or {}
            shortcuts = raw.get("shortcuts", raw) if isinstance(raw, dict) else {}
        steam_root = await asyncio.to_thread(resolve_live_steam_root)
        return steam_root, shortcuts

    async def scan_stale_compatdata(self) -> dict[str, Any]:
        """Classified inventory of non-Steam ``compatdata`` directories.

        Returns ``entries`` (each with ``app_id``, ``name``,
        ``classification``, ``size_bytes``, ``deletable``) plus
        ``deletable_count`` / ``deletable_bytes`` for the confirmation UI.
        Directories owned by the user's own shortcuts are reported with
        ``deletable=False`` so they are visible but never actionable.
        """
        steam_root, shortcuts = await self._compatdata_inputs()
        result = await asyncio.to_thread(compatdata_scan.scan, steam_root, shortcuts)
        logger.info(
            "[compatdata] scan: %d entries, %d deletable (%.1f GB)",
            len(result["entries"]), result["deletable_count"],
            result["deletable_bytes"] / 2**30,
        )
        return result

    async def delete_stale_compatdata(
        self, app_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Delete stale prefixes, re-verifying each against a fresh scan.

        ``app_ids`` narrows the deletion to a user-chosen subset; ``None``
        means "everything the scan considers deletable". Anything not
        deletable per the fresh scan is ignored and counted in ``refused``.
        """
        steam_root, shortcuts = await self._compatdata_inputs()
        result = await asyncio.to_thread(compatdata_scan.scan, steam_root, shortcuts)

        allowed = {e["app_id"]: e for e in result["entries"] if e["deletable"]}
        requested = set(app_ids) if app_ids is not None else set(allowed)
        refused = sorted(requested - set(allowed))
        if refused:
            logger.warning(
                "[compatdata] refusing to delete non-deletable app_ids: %s", refused,
            )

        deleted, freed = [], 0
        for app_id in sorted(requested & set(allowed)):
            entry = allowed[app_id]
            if await asyncio.to_thread(safe_rmtree, entry["path"]):
                deleted.append(app_id)
                freed += entry["size_bytes"]
            else:
                logger.warning("[compatdata] delete failed for %s", entry["path"])

        logger.info(
            "[compatdata] deleted %d dirs, freed %.1f GB",
            len(deleted), freed / 2**30,
        )
        return {
            "deleted_count": len(deleted),
            "deleted_app_ids": deleted,
            "freed_bytes": freed,
            "refused_count": len(refused),
        }
