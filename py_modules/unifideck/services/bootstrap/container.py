"""Service container — typed registry of all Layer-5 services.

OP-13b | py_modules/unifideck/services/bootstrap/container.py

``ServiceContainer`` holds one reference per service constructed at
boot time. It's a simple typed bag — no dependency-injection magic,
just a place to find any service by attribute name with full type
hints for IDEs.

Services that depend on other services receive their dependencies as
constructor arguments (not by reaching into the container at runtime),
keeping each service independently testable.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    """Typed bag holding every Layer-5 service constructed at boot.

    Each field is one service slot; the constructor leaves them all
    at ``None`` and the ``bootstrap_services`` function in
    ``constructor.py`` (OP-13d) populates them in order. After boot
    every field is either a service instance or ``None`` (if the
    service was opt-out via configuration).

    Storing services on a dataclass (rather than in a dict) gives
    IDEs and type checkers visibility into ``container.shortcut``,
    ``container.download``, etc., and makes refactors safe.
    """

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
    microsoft_subscription: MicrosoftSubscriptionService | None = None
