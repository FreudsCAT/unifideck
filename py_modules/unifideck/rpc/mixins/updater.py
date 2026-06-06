"""Updater RPC mixin for Plugin class.

Exposes plugin self-update operations to the frontend:
- check_plugin_update: version comparison
- get_available_versions: all installable releases
- get_release_notes: markdown body for a specific version

Installation is handled frontend-side by calling Decky's
``utilities/install_plugin`` directly — this mixin only
provides the metadata needed to populate the UI and construct
the install call.
"""
from __future__ import annotations

import logging
from typing import Any

from unifideck.rpc.errors import RpcError

logger = logging.getLogger(__name__)


class UpdaterRPCMixin:
    """Plugin self-update RPC surface.

    Requires ``self._updater_service`` to be set by
    ``boot_plugin`` before any of these methods are called.
    """

    _updater_service: Any

    async def check_plugin_update(self) -> dict[str, Any]:
        """Check if a newer plugin version is available.

        Returns::

            {
                "available": bool,
                "current": "0.6.1",
                "latest": {version, name, asset_url, sha256, ...} | None,
            }

        Called on boot, QAM mount, manual trigger, and every
        6 hours by the background poller.
        """
        svc = getattr(self, "_updater_service", None)
        if svc is None:
            raise RpcError("service_unavailable", service="updater")
        return await svc.check_for_update()

    async def get_available_versions(self) -> list[dict[str, Any]]:
        """Return all installable versions from GitHub releases.

        Only versions with downloadable ``.zip`` assets are
        returned. Includes prerelease / dev builds. Sorted
        newest-first.
        """
        svc = getattr(self, "_updater_service", None)
        if svc is None:
            raise RpcError("service_unavailable", service="updater")
        releases = await svc.fetch_releases()
        return [r.to_dict() for r in releases]

    async def get_release_notes(self, version: str) -> str:
        """Get release notes (markdown body) for a specific version.

        Args:
            version: semver string like ``"0.6.1"`` or tag-derived
                string like ``"Dev"``.

        Returns the raw markdown body from the GitHub release, or
        an empty string if the version is not found.
        """
        svc = getattr(self, "_updater_service", None)
        if svc is None:
            raise RpcError("service_unavailable", service="updater")
        # Ensure cache is populated
        await svc.fetch_releases()
        release = svc.get_release_for_version(version)
        if release is None:
            return ""
        return release.body
