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
    # ``cdp_utils`` is an optional helper module that may not be
    # present on every environment — the try/except below is the
    # runtime guard. ``# type: ignore[import-not-found]`` tells
    # mypy strict that the missing module is intentional, not a
    # bug.
    from .cdp_utils import create_cef_debugging_flag  # type: ignore[import-not-found]
except ImportError:
    create_cef_debugging_flag = None


__all__ = [
    "SteamCSSInjector",
    "create_cef_debugging_flag",
    "get_cdp_client",
    "shutdown_cdp_client",
]
