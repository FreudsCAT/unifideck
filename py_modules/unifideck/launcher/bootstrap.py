from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Any
logger = logging.getLogger(__name__)
def build_launcher_service(config: Any | None = None) -> Any:
    """Build launcher service."""
    from ..auth.edge_browser import EdgeBrowser
    from ..event_bus import EventBus
    from ..services.bootstrap import (
        ServicePaths,
        build_service_subset,
    )
    from ..services.launcher import LauncherService
    if config is None:
        config = _load_standalone_config()
        bus = EventBus()
        paths = ServicePaths.from_config(config)
        services = build_service_subset(
            bus, config, paths,
            attrs={"shortcut", "proton", "cloudsave", "launch_history"},
        )
        shortcut_svc = services.get("shortcut")
        proton_svc = services.get("proton")
        cloud_svc = services.get("cloudsave")
        assert shortcut_svc is not None, "bootstrap: shortcut service missing"
        assert proton_svc is not None, "bootstrap: proton service missing"
        assert cloud_svc is not None, "bootstrap: cloudsave service missing"
        edge_browser = EdgeBrowser(
            cdp_port=config.get_int("edge.cdp_port", 9222),
            locale_fn=lambda: config.get_str("ui.locale", "en-US"),
        )
        return LauncherService(
            bus=bus,
            shortcut_svc=shortcut_svc,
            proton_svc=proton_svc,
            cloud_svc=cloud_svc,
            edge_browser=edge_browser,
            config=config,
        )
def _load_standalone_config() -> Any:
    """Load standalone config."""
    from ..config.config_manager import ConfigManager
    plugin_dir = _resolve_plugin_dir()
    defaults_path = os.path.join(
        plugin_dir, "defaults", "config.json",
    )
    user_path = _user_config_path()
    return ConfigManager(
        defaults_path=defaults_path,
        user_path=user_path,
    )
def _resolve_plugin_dir() -> str:
    """Resolve plugin dir."""
    from ..core.paths import resolve_plugin_dir
    return str(resolve_plugin_dir(start=Path(__file__)))

def _user_config_path() -> str | None:

    """User config path."""
    override = os.environ.get("UNIFIDECK_USER_CONFIG")
    if override:
        return override
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(
        Path.home() / ".config",
    )
    return str(Path(xdg) / "unifideck" / "config.json")