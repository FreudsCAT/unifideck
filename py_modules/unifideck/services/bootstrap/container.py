"""services/bootstrap/container.py — Dependency injection container.

Holds typed references to all service instances. Used as the single injection
point — main.py creates one and passes it to RPC handlers, or test harnesses
can create one with a subset of services.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...auth.browser import OAuthBrowserMonitor
    from ...auth.edge_browser import EdgeBrowser
    from ...cdp.cdp_client import CDPClient
    from ...core.metrics_collector import MetricsCollector
    from ..account_service import AccountService
    from ..artwork import ArtworkService
    from ..cloud_save import CloudSaveService
    from ..download import DownloadService
    from ..feature_flag_service import FeatureFlagService
    from ..launch_history import LaunchHistoryService
    from ..metadata_service import MetadataService
    from ..microsoft_subscription import (
        MicrosoftSubscriptionService,
    )
    from ..playtime import PlaytimeService
    from ..probe_reaction_service import ProbeReactionService
    from ..proton_service import ProtonService
    from ..security import SecurityService
    from ..shortcut import ShortcutService


@dataclass
class ServiceContainer:
    """Dependency injection container holding all service instances."""

    shortcut: ShortcutService | None = None
    download: DownloadService | None = None
    metadata: MetadataService | None = None
    artwork: ArtworkService | None = None
    proton: ProtonService | None = None
    cdp: CDPClient | None = None
    cloudsave: CloudSaveService | None = None
    metrics: MetricsCollector | None = None
    account: AccountService | None = None
    playtime: PlaytimeService | None = None
    feature_flags: FeatureFlagService | None = None
    probe_reaction: ProbeReactionService | None = None
    security: SecurityService | None = None
    launch_history: LaunchHistoryService | None = None
    launch_logs: LaunchLogsService | None = None
    microsoft_subscription: MicrosoftSubscriptionService | None = None
    # OAuth browser monitor — shared CDP-based redirect watcher
    # consumed by every store's `AuthOrchestrator`. Injected into
    # stores via `store_injector._STORE_INJECTIONS`.
    browser_monitor: OAuthBrowserMonitor | None = None
    # Edge browser — flatpak installer + CDP launcher used by
    # the four OAuth stores (Epic / GOG / Amazon / Microsoft).
    # Constructed once per plugin and shared via the injector.
    edge_browser: EdgeBrowser | None = None
