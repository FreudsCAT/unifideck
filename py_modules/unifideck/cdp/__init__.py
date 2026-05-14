"""cdp — Chrome DevTools Protocol client and Steam-CSS injector.

Re-exports the public API:

* :class:`SteamCSSInjector` for pushing CSS into the Steam client.
* :func:`get_cdp_client` / :func:`shutdown_cdp_client` for the
  shared async websocket client.
* :func:`create_cef_debugging_flag` — best-effort optional helper
  that may not be available on all environments; the try/except
  swallows the ImportError so the package still loads.
"""

from __future__ import annotations

from .cdp_inject import (
    SteamCSSInjector,
    get_cdp_client,
    shutdown_cdp_client,
)

try:
    from .cdp_utils import create_cef_debugging_flag
except ImportError:
    create_cef_debugging_flag = None  # type: ignore[assignment]


__all__ = [
    "SteamCSSInjector",
    "create_cef_debugging_flag",
    "get_cdp_client",
    "shutdown_cdp_client",
]
