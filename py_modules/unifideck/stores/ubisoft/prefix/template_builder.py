"""template_builder.py — Manage the shared Ubisoft prefix template.

# OP-59c | py_modules/unifideck/stores/ubisoft/prefix/template_builder.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import UbisoftConfig
    from ..installer.cache import UbisoftInstallerCache
    from ..paths import UbisoftPrefixPaths
    from .helpers import _PrefixHelpers

logger = logging.getLogger(__name__)
_TEMPLATE_VERSION_KEY = 'template_version'
_TEMPLATE_VERSION = 3
_PROTON_VERSION_RE = re.compile(r'(\d+(?:\.\d+)*)')


class _TemplatePrefixBuilder:
    """Template prefix builder."""

    def __init__(
        self, *,
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
        self._creation_task: asyncio.Task[None] | None = None

    def template_exists(self) -> bool:
        """Template exists."""
        template = self._config.template_dir_expanded
        upc = self._paths.find_upc_exe(template)
        return bool(upc)

    def is_prefix_version_stale(self, prefix_dir: str) -> bool:
        """Check whether prefix version stale."""
        marker = os.path.join(prefix_dir, self._config.bootstrap_marker)
        if not os.path.isfile(marker):
            return True
        try:
            with open(marker, encoding='utf-8') as f:
                for line in f:
                    if '=' not in line:
                        continue
                    key, _, value = line.strip().partition('=')
                    if key == _TEMPLATE_VERSION_KEY:
                        try:
                            return int(value) < _TEMPLATE_VERSION
                        except ValueError:
                            return True
        except OSError:
            return True
        return True

    @staticmethod
    def read_machine_guid(prefix_path: str) -> str:
        """Read machine guid."""
        for reg in ('system.reg', os.path.join('pfx', 'system.reg')):
            path = os.path.join(prefix_path, reg)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        m = re.search(
                            r'"MachineGuid"="([^"]+)"', line, re.IGNORECASE,
                        )
                        if m:
                            return m.group(1)
            except OSError:
                continue
        return ''

    def queue_template_creation(self) -> None:
        """Queue template creation."""
        if self._creation_task is not None and not self._creation_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._creation_task = loop.create_task(
            self.regenerate_template_if_stale(),
            name='ubisoft_template_create',
        )

    async def regenerate_template_if_stale(self) -> None:
        """Regenerate template if stale."""
        if (
            self.template_exists()
            and not self.is_prefix_version_stale(
                self._config.template_dir_expanded,
            )
        ):
            return
        await self.ensure_template_prefix()

    async def ensure_template_prefix(self) -> None:
        """Ensure template prefix."""
        template = self._config.template_dir_expanded
        if self.template_exists() and not self.is_prefix_version_stale(template):
            return
        if os.path.isdir(template):
            shutil.rmtree(template, ignore_errors=True)
        await self._helpers.create_prefix_from_fresh_install(
            space_id='template', prefix_path=template,
        )
        self._helpers.write_bootstrap_marker(
            template, source='template_builder', space_id='template',
        )
        self._stamp_template_version(template)

    def _stamp_template_version(self, template: str) -> None:
        """Stamp template version into the bootstrap marker."""
        marker = Path(template) / self._config.bootstrap_marker
        try:
            with open(marker, 'a', encoding='utf-8') as f:
                f.write(f'{_TEMPLATE_VERSION_KEY}={_TEMPLATE_VERSION}\n')
        except OSError as e:
            logger.debug('[Ubisoft.prefix] template version stamp: %s', e)
