# OP-05 | core/types/__init__.py | Depends: OP-05a, OP-05b, OP-05c
from __future__ import annotations

from .domain import (
    CLITool,
    Game,
    StoreInfo,
)
from .events import (
    ErrorCode,
    Events,
    GameTag,
    OwnershipType,
    StoreEnum,
    StoreStatus,
    SubscriptionTier,
)
from .results import (
    AccountResult,
    ArtworkResult,
    AuthResult,
    CloudSaveResult,
    DownloadResult,
    InstallResult,
    MetadataResult,
    PlaytimeResult,
    Result,
    StoreAuthError,
    StoreDownloadError,
    StoreError,
    StoreSyncError,
    SyncResult,
)

__all__ = [
    "ErrorCode", "Events", "GameTag", "OwnershipType",
    "StoreEnum", "StoreStatus", "SubscriptionTier",

    "AccountResult", "ArtworkResult", "AuthResult",
    "CloudSaveResult", "DownloadResult", "InstallResult",
    "MetadataResult", "PlaytimeResult", "Result",
    "StoreAuthError", "StoreDownloadError", "StoreError",
    "StoreSyncError", "SyncResult",

    "CLITool", "Game", "StoreInfo",
]
