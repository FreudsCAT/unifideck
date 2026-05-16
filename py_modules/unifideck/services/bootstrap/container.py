"""services/bootstrap/container.py — Dependency injection container.

Holds typed references to all service instances. Used as the single injection
point — main.py creates one and passes it to RPC handlers, or test harnesses
can create one with a subset of services.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unifideck.auth.browser import OAuthBrowserMonitor
    from unifideck.auth.edge_browser import EdgeBrowser
    from unifideck.cdp.cdp_client import CDPClient
    from unifideck.core.metrics_collector import MetricsCollector
    from unifideck.services.account_service import AccountService
    from unifideck.services.artwork import ArtworkService
    from unifideck.services.cloud_save import CloudSaveService
    from unifideck.services.download import DownloadService
    from unifideck.services.feature_flag_service import FeatureFlagService
    from unifideck.services.launch_history import LaunchHistoryService
    from unifideck.services.launch_logs import LaunchLogsService
    from unifideck.services.metadata_service import MetadataService
    from unifideck.services.microsoft_subscription import MicrosoftSubscriptionService
    from unifideck.services.playtime import PlaytimeService
    from unifideck.services.probe_reaction_service import ProbeReactionService
    from unifideck.services.proton_service import ProtonService
    from unifideck.services.security import SecurityService
    from unifideck.services.shortcut import ShortcutService


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
