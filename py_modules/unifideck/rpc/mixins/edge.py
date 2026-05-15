"""EdgeRPCMixin — Microsoft Edge install + readiness RPCs.

OP-26m | py_modules/unifideck/rpc/mixins/edge.py

Browser-based store auth (Epic / GOG / Amazon / Microsoft)
opens the OAuth URL inside a Wine-prefixed instance of
Microsoft Edge (flatpak ``com.microsoft.Edge``). The
prerequisite isn't always installed on a fresh Steam Deck —
the frontend needs a way to:

  1. detect whether Edge is installed before triggering an
     auth attempt (so the user doesn't see "launcher crashed"
     out of nowhere) ;
  2. trigger an install via flatpak when it's missing, with
     a spinner + result toast.

The PDF spec ships ``EdgeInstaller`` (OP-29e) and
``MicrosoftStore.install_edge`` (OP-53) but never exposes
either as an RPC ; this mixin closes the gap. It proxies
through the Microsoft store because that's where the
``EdgeBrowser`` singleton lives — all stores share the same
flatpak install so reaching it from one store is enough.

If the Microsoft store hasn't been registered (test installs,
opted-out builds) the mixin returns structured non-success
responses rather than raising — the frontend treats both
``installed: false`` and ``unavailable`` as "show the modal".
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class EdgeRPCMixin:
    """Edge prereq RPC : install + check."""

    registry: Any

    async def is_edge_installed(self) -> Any:
        """Probe whether the Edge flatpak is available.

        Returns:
            ``{installed: bool}``. Always reports
            ``installed: false`` if the Microsoft store
            isn't registered (no `EdgeBrowser` instance to
            ask) — the frontend treats that the same as
            "not installed" for UX purposes.
        """
        store = self.registry.get("microsoft")
        if store is None or not hasattr(store, "is_edge_installed"):
            logger.info(
                "[EdgeRPC] is_edge_installed=False "
                "(microsoft store unavailable)",
            )
            return {"installed": False}
        installed = bool(store.is_edge_installed())
        logger.info("[EdgeRPC] is_edge_installed=%s", installed)
        return {"installed": installed}

    async def install_edge(self) -> Any:
        """Install the Edge flatpak via Microsoft store's helper.

        Wraps ``MicrosoftStore.install_edge``. The flatpak
        install can take 30-90 s on a fresh prefix ; the
        frontend modal shows a spinner during the call.

        Returns:
            ``{installed: bool, error: str | None}`` as the
            data payload. Returning a dict that does **not**
            start with a ``success`` key keeps ``_to_envelope``
            from treating this as a caller-supplied envelope
            (which would collapse ``data`` to ``None`` and
            break frontend unwrapping). ``error`` is populated
            on failure (``"edge_browser_not_configured"``,
            ``"flatpak_not_found"``, etc).
        """
        store = self.registry.get("microsoft")
        if store is None or not hasattr(store, "install_edge"):
            logger.warning(
                "[EdgeRPC] install_edge unavailable "
                "(microsoft store missing)",
            )
            return {
                "installed": False,
                "error": "microsoft_store_unavailable",
            }
        logger.info("[EdgeRPC] install_edge: starting flatpak install")
        result = await store.install_edge()
        success = bool(getattr(result, "success", False))
        error = getattr(result, "error", None)
        logger.info(
            "[EdgeRPC] install_edge result: success=%s error=%s",
            success, error,
        )
        return {"installed": success, "error": error}
