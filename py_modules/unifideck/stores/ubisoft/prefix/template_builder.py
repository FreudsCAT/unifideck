"""
Template prefix builder — install UPC into a clean prefix.

OP-59c | py_modules/unifideck/stores/ubisoft/prefix/template_builder.py

``_TemplateBuilder`` constructs the ``.template`` prefix: a freshly
created Wine prefix with UPC pre-installed. It's used as the base for
all per-game prefixes — copying the template is much faster than
running the UPC installer every time.

Build steps:

1. ``proton run`` create-prefix to initialise a fresh prefix;
2. tweak the registry (disable mshtml, configure mountpoints);
3. run the cached UPC installer in unattended mode;
4. wait for UPC's first-launch to settle;
5. write the bootstrap marker;
6. shut UPC down gracefully.

If any step fails the partial prefix is removed and the caller gets
an explicit error code identifying the failing step.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from unifideck.stores.ubisoft.binaries import UbisoftBinaryResolver

if TYPE_CHECKING:
    from unifideck.stores.ubisoft.config import UbisoftConfig
    from unifideck.stores.ubisoft.installer.cache import UbisoftInstallerCache
    from unifideck.stores.ubisoft.paths import UbisoftPrefixPaths

    from .helpers import _PrefixHelpers
logger = logging.getLogger(__name__)


class _TemplatePrefixBuilder:
    """Template prefix builder."""

    def __init__(
        self,
        *,
        config: UbisoftConfig,
        paths: UbisoftPrefixPaths,
        helpers: _PrefixHelpers,
        installer_cache: UbisoftInstallerCache,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths
        self._helpers = helpers
        self._installer_cache = installer_cache
        self._template_task: asyncio.Task[None] | None = None

    def template_exists(self) -> bool:
        """Template exists."""
        marker = (
            Path(self._config.template_dir_expanded) / self._config.bootstrap_marker
        )
        return marker.is_file()

    def is_prefix_version_stale(self, prefix_dir: str) -> bool:
        """Check whether prefix version stale."""
        version_file = Path(prefix_dir) / "version"
        if not version_file.is_file():
            return False
        try:
            prefix_version = version_file.read_text(
                encoding="utf-8",
            ).strip()
        except OSError:
            return False
        if not prefix_version:
            return False
        family = UbisoftBinaryResolver.proton_family(
            prefix_version,
        )
        if family != "experimental":
            logger.info(
                "[UbisoftPrefixManager] prefix stale: '%s' "
                "(family=%s, expected=experimental) prefix=%s",
                prefix_version,
                family,
                prefix_dir,
            )
            return True
        return False

    @staticmethod
    def read_machine_guid(prefix_path: str) -> str:
        """Read machine guid."""
        prefix_p = Path(prefix_path)
        for reg_path in (
            prefix_p / "pfx" / "system.reg",
            prefix_p / "system.reg",
        ):
            if not reg_path.is_file():
                continue
            try:
                content = reg_path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )
            except OSError:
                continue
            match = re.search(
                r'"MachineGuid"="([^"]+)"',
                content,
            )
            if match:
                return match.group(1)
        return ""

    def queue_template_creation(self) -> None:
        """Queue template creation."""
        if self._template_task is not None and not self._template_task.done():
            logger.info(
                "[UbisoftPrefixManager] template creation already in progress",
            )
            return
        logger.info(
            "[UbisoftPrefixManager] queuing background template creation",
        )
        self._template_task = asyncio.create_task(
            self.ensure_template_prefix(),
        )

    async def regenerate_template_if_stale(self) -> None:
        """Regenerate template if stale."""
        if not self.template_exists():
            return
        template_dir = self._config.template_dir_expanded
        if not self.is_prefix_version_stale(template_dir):
            return
        logger.warning(
            "[UbisoftPrefixManager] template prefix stale, removing for recreation",
        )
        shutil.rmtree(template_dir, ignore_errors=True)

    async def ensure_template_prefix(self) -> None:
        """Ensure template prefix."""
        if self.template_exists():
            logger.info(
                "[UbisoftPrefixManager] template already exists",
            )
            return
        template_dir = self._config.template_dir_expanded
        logger.info(
            "[UbisoftPrefixManager] creating template prefix",
        )
        try:
            installer_path = await self._installer_cache.ensure_cached()
            if not installer_path:
                logger.error(
                    "[UbisoftPrefixManager] installer cache "
                    "failed, aborting template creation",
                )
                return
            await asyncio.to_thread(lambda: Path(template_dir).mkdir(parents=True, exist_ok=True))
            success = await self._helpers.run_silent_installer(
                prefix_dir=template_dir,
                installer_path=installer_path,
                gameid="umu-ubisoft-template",
            )
            if not success:
                return
            if not self._paths.find_upc_exe(template_dir):
                logger.error(
                    "[UbisoftPrefixManager] upc.exe not found after template install",
                )
                return
            self._helpers.write_bootstrap_marker(
                template_dir,
                "template",
                None,
            )
            self._helpers.try_inject_auth_state([template_dir])
            logger.info(
                "[UbisoftPrefixManager] template created successfully",
            )
        except Exception:
            logger.exception("[UbisoftPrefixManager] template creation failed")
