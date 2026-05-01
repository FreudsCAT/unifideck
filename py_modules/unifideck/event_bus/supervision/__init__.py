"""event_bus.supervision — EventBus handler supervision primitives.

Groups the two per-handler supervisory components that wrap the
user-registered callbacks before they reach the dispatcher:

  - ``watchdog_handler`` : timeout + quarantine detection. Aborts
    handlers that exceed their budget and quarantines repeat
    offenders so one slow consumer can't poison the whole bus.
  - ``metrics_handler`` : per-handler latency collector with a
    bounded histogram ring, consumed by the observability RPC
    surface to surface top-N slow handlers without external APM.

Name chosen over ``handlers/`` to avoid confusion with
``rpc/handlers/`` which is a different concern (RPC method
group implementations, not supervisory infrastructure). These
modules don't define handlers — they supervise the handlers
that users register with the EventBus.

Public API (re-exported at the ``event_bus`` package level via
``event_bus/__init__.py``) is unchanged — callers importing
``from unifideck.event_bus import HandlerWatchdog`` still work.
"""
from __future__ import annotations

from .metrics_handler import HandlerLatencyCollector  # noqa: F401
from .watchdog_handler import HandlerWatchdog  # noqa: F401
