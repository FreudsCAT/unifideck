"""RPC handlers — concrete method implementations grouped by domain.

OP-25 | py_modules/unifideck/rpc/handlers/__init__.py

Each handler class inherits from ``RpcHandlerBase`` (which
holds the common dependency injection slots — bus, registry,
cache, config, sync, services) and exposes one or more public
coroutine methods that get auto-wrapped by ``rpc_wrapper`` at
import time via ``@auto_wrap_rpc_methods``.

Domain split:

* ``ActionHandlers``        — ``unifideck://`` deep-link verbs
  (auth, retry-sync, refresh-library, refresh-all-libraries);
* ``DownloadHandlers``      — download queue CRUD;
* ``LaunchHandlers``        — launch + circuit-breaker bypass;
* ``ObservabilityHandlers`` — diagnostics / metrics readouts;
* ``SecurityHandlers``      — audit log + brute-force state;
* ``StoreHandlers``         — store list, library, install,
  uninstall, sync triggers;
* ``UIHandlers``            — frontend-state accessors
  (locales, themes, version info).

Re-exports every group + the base class so consumers can do
``from rpc.handlers import StoreHandlers`` without knowing the
internal module split.
"""

from __future__ import annotations

from unifideck.rpc.handlers.action_handlers import ActionHandlers
from unifideck.rpc.handlers.base import RpcHandlerBase
from unifideck.rpc.handlers.download_handlers import DownloadHandlers
from unifideck.rpc.handlers.launch_handlers import LaunchHandlers
from unifideck.rpc.handlers.observability_handlers import (
    ObservabilityHandlers,
)
from unifideck.rpc.handlers.security_handlers import SecurityHandlers
from unifideck.rpc.handlers.store_handlers import StoreHandlers
from unifideck.rpc.handlers.ui_handlers import UIHandlers

__all__ = [
    "ActionHandlers",
    "DownloadHandlers",
    "LaunchHandlers",
    "ObservabilityHandlers",
    "RpcHandlerBase",
    "SecurityHandlers",
    "StoreHandlers",
    "UIHandlers",
]
