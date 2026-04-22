from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unifideck.event_bus.bus_pipeline import BusPipeline

DECKY_PLUGIN_DIR = os.environ.get(
    "DECKY_PLUGIN_DIR", os.path.dirname(__file__),
)

sys.path.insert(0, os.path.join(DECKY_PLUGIN_DIR, "py_modules"))

from unifideck.config.user_config_path import resolve_user_config_path
from unifideck.rpc import auto_wrap_rpc_methods
from unifideck.rpc.mixins.action import ActionRPCMixin
from unifideck.rpc.mixins.cloud_failure import CloudFailureRPCMixin
from unifideck.rpc.mixins.config_validation import ConfigValidationRPCMixin
from unifideck.rpc.mixins.download import DownloadRPCMixin
from unifideck.rpc.mixins.launch import LaunchRPCMixin
from unifideck.rpc.mixins.observability import ObservabilityRPCMixin
from unifideck.rpc.mixins.playtime import PlaytimeRPCMixin
from unifideck.rpc.mixins.security import SecurityRPCMixin
from unifideck.rpc.mixins.store import StoreRPCMixin
from unifideck.rpc.mixins.sync import SyncRPCMixin
from unifideck.rpc.mixins.ui import UIRPCMixin

logger = logging.getLogger(__name__)


@auto_wrap_rpc_methods
class Plugin(
    ObservabilityRPCMixin,
    SecurityRPCMixin,
    DownloadRPCMixin,
    LaunchRPCMixin,
    StoreRPCMixin,
    SyncRPCMixin,
    UIRPCMixin,
    CloudFailureRPCMixin,
    ConfigValidationRPCMixin,
    PlaytimeRPCMixin,
    ActionRPCMixin,
):
    async def _main(self) -> None:
        from unifideck.bootstrap.boot import boot_plugin
        await boot_plugin(
            self,
            decky_plugin_dir=DECKY_PLUGIN_DIR,
            user_config_path_resolver=resolve_user_config_path,
        )

    async def _validate_config(self) -> None:
        from unifideck.config.startup import validate_config_at_startup
        defaults_path = os.path.join(
            DECKY_PLUGIN_DIR, "defaults", "config.json",
        )
        (
            self._config_validation_result,
            self._config_degraded,
        ) = await validate_config_at_startup(
            bus=self.bus,
            config=self.config,
            defaults_path=defaults_path,
            user_config_path=self._user_config_path,
        )

    async def _build_eventbus_pipeline(self) -> BusPipeline:
        from unifideck.bootstrap.pipeline_factory import (
            build_eventbus_pipeline,
        )
        return await build_eventbus_pipeline(self)

    async def _unload(self) -> None:
        from unifideck.bootstrap.teardown import unload_plugin
        await unload_plugin(self)

    def _register_caches(self) -> None:
        from unifideck.bootstrap.cache_registry import (
            register_default_caches,
        )
        register_default_caches(self.cache)
