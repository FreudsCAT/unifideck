"""Unifideck — unified game library for Steam Deck.

Top-level package. Defines the public API of the plugin's backend,
accessible from any Decky-loaded code via simple
`from unifideck.X import Y` imports.

The new architecture is organized in 5 layers:
    Layer 1 — core/types (Game, Result, Events, ...)
    Layer 2 — core/services (EventBus, CacheManager, StoreRegistry,
              SyncService, BinaryResolver, ExeFinder)
    Layer 3 — stores/store_base (StoreBase ABC + 5 abstract methods)
    Layer 4 — stores/ (5 store plugins, one per backend)
    Layer 5 — services/ (infrastructure services subscribing to EventBus)

Adjacent packages (`auth/`, `cdp/`, `compatibility/`, `metadata/`,
`steam/`, `utils/`) provide support modules.
"""
from __future__ import annotations

__version__ = "0.7.1"
