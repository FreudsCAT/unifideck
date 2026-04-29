"""core/types/__init__.py — Barrel re-export for the types package.

Addition policy:
  - New event → events.py
  - New result type → results.py
  - New domain entity → domain.py
  - New cross-cutting type (TypedDict, Protocol) → create a new
    sub-module and re-export from here, don't dump into the
    existing files

The `__all__` list is the contract: anything not in it is
considered internal and may move between sub-modules without
warning. Anything in __all__ is stable and requires a
deprecation cycle to remove.
"""
from __future__ import annotations

# Domain dataclasses
from .domain import (
    CLITool,
    Game,
    StoreInfo,
)

# Enums
from .events import (
    ErrorCode,
    Events,
    GameTag,
    OwnershipType,
    StoreEnum,
    StoreStatus,
    SubscriptionTier,
)

# Result dataclasses + exception hierarchy
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
    # events.py
    "ErrorCode", "Events", "GameTag", "OwnershipType",
    "StoreEnum", "StoreStatus", "SubscriptionTier",
    # results.py
    "AccountResult", "ArtworkResult", "AuthResult",
    "CloudSaveResult", "DownloadResult", "InstallResult",
    "MetadataResult", "PlaytimeResult", "Result",
    "StoreAuthError", "StoreDownloadError", "StoreError",
    "StoreSyncError", "SyncResult",
    # domain.py
    "CLITool", "Game", "StoreInfo",
]
