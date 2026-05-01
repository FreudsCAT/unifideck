"""unifideck.rpc.mixins — RPC mixin classes for Plugin."""
from __future__ import annotations

from .action import ActionRPCMixin
from .cloud_failure import CloudFailureRPCMixin
from .config_validation import ConfigValidationRPCMixin
from .download import DownloadRPCMixin
from .launch import LaunchRPCMixin
from .observability import ObservabilityRPCMixin
from .playtime import PlaytimeRPCMixin
from .security import SecurityRPCMixin
from .store import StoreRPCMixin
from .sync import SyncRPCMixin
from .ui import UIRPCMixin

__all__ = [
    "ActionRPCMixin",
    "CloudFailureRPCMixin",
    "ConfigValidationRPCMixin",
    "DownloadRPCMixin",
    "LaunchRPCMixin",
    "ObservabilityRPCMixin",
    "PlaytimeRPCMixin",
    "SecurityRPCMixin",
    "StoreRPCMixin",
    "SyncRPCMixin",
    "UIRPCMixin",
]
