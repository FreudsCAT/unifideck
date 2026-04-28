"""unifideck.core — Layers 1-3 of the new architecture.

This package contains the foundational types and services that
the rest of the codebase builds on. It has **zero external
dependencies** beyond Python's stdlib (no aiohttp, no decky, no
Steam-specific code), so any module here can be unit-tested in
complete isolation.

Layer 1 — Core types (no logic, just dataclasses):
  - types : Game, Result, AuthResult, SyncResult, Events, ...
  - async_file_ops: thin wrappers around asyncio.to_thread

Layer 2 — Core services (stateless infrastructure):
  - event_bus : pub/sub with error isolation
  - cache_manager : 9 named caches with TTL + atomic writes
  - binary_resolver : 3-tier CLI binary discovery
  - exe_finder : game executable detection
  - config_manager : MOVED to unifideck.config
  - store_registry : MOVED to unifideck.stores
  - sync_service : generic library sync loop

Layer 3 — Store base abstraction:
  - store_base : MOVED to unifideck.stores

The new architecture imports from this package upward but never
downward — there are no circular dependencies into stores/ or
services/. This is enforced by the integration smoke test.

Reference: Technical Document v1.0 — Sections 3.1.1 (Core types),
3.1.2 (Core services), 3.1.3 (StoreBase).
"""

# Re-export the most-used types so callers can write
# `from unifideck.core import Events` instead of the longer
# `from unifideck.core.types import Events`. Only the names that
# appear in 5+ call sites across the codebase are promoted here —
# anything more specialized stays in its submodule to avoid
# polluting the namespace.
from .cache_manager import CacheManager  # noqa: F401

# ConfigManager moved to unifideck.config. No shim:
# callers must now import `from unifideck.config import ConfigManager`.
# StoreBase and StoreRegistry moved to unifideck.stores — same
# clean-break pattern. (StoreBaseInjected removed 2026-04-20 as
# dead code.)
#
# Note: ``SyncService`` lives at ``unifideck.core.sync_service`` but is
# NOT re-exported here because that would create a circular import
# (sync_service → event_bus → core.types → core.__init__ → sync_service).
# Callers must use the fully-qualified path:
#     from unifideck.core.sync_service import SyncService
from .types import (  # noqa: F401
    AuthResult,
    CLITool,
    DownloadResult,
    Events,
    Game,
    InstallResult,
    Result,
    StoreError,
    StoreInfo,
    StoreStatus,
    SyncResult,
)
