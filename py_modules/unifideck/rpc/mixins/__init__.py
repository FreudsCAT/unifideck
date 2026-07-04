"""RPC mixin classes — alternative composition path to handler groups.

OP-26 | py_modules/unifideck/rpc/mixins/__init__.py

The mixins are the **older** composition style for the RPC
surface: each one declares a few methods that get mixed into
the plugin class via multiple inheritance, rather than being
attached at runtime via ``composer.bind_handlers``.

Both styles coexist in v1.3 for migration reasons; new RPC
methods should be added to a handler group (``handlers/``)
rather than a mixin. The mixins remain wired in for backward
compatibility with code that already imports them.

Per-mixin scope:

* ``ActionRPCMixin``         — ``unifideck://`` URI dispatch;
* ``CloudFailureRPCMixin``   — cloud-failure UX configuration;
* ``ConfigValidationRPCMixin`` — config-validation status;
* ``DownloadRPCMixin``       — download-queue management;
* ``LaunchRPCMixin``         — launch / circuit breaker;
* ``ObservabilityRPCMixin``  — metrics, watchdog, replay;
* ``PlaytimeRPCMixin``       — per-game playtime stats;
* ``SecurityRPCMixin``       — audit log + brute-force state;
* ``StoreRPCMixin``          — auth + login state;
* ``SyncRPCMixin``           — library sync + game info;
* ``UIRPCMixin``             — Steam-UI manipulation + locale.

Re-exports every mixin class as a public name.
"""

from __future__ import annotations

from .action import ActionRPCMixin
from .cloud_failure import CloudFailureRPCMixin
from .cloud_save import CloudSaveRPCMixin
from .config_validation import ConfigValidationRPCMixin
from .download import DownloadRPCMixin
from .launch import LaunchRPCMixin
from .observability import ObservabilityRPCMixin
from .playtime import PlaytimeRPCMixin
from .security import SecurityRPCMixin
from .store import StoreRPCMixin
from .sync import SyncRPCMixin
from .ui import UIRPCMixin
from .updater import UpdaterRPCMixin

__all__ = [
    "ActionRPCMixin",
    "CloudFailureRPCMixin",
    "CloudSaveRPCMixin",
    "ConfigValidationRPCMixin",
    "DownloadRPCMixin",
    "LaunchRPCMixin",
    "ObservabilityRPCMixin",
    "PlaytimeRPCMixin",
    "SecurityRPCMixin",
    "StoreRPCMixin",
    "SyncRPCMixin",
    "UIRPCMixin",
    "UpdaterRPCMixin",
]
