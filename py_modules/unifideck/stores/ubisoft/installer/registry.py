"""registry.py — Wine registry surgery for installs / shortcuts.

# OP-56f | py_modules/unifideck/stores/ubisoft/installer/registry.py | Depends: (none)

Two unrelated concerns share this module per the OP-56f spec:

1. Module-level functions that read & rewrite the per-prefix
   ``system.reg`` to inject or remove install entries.
2. ``_ShortcutRegistry`` — a thin reader of unifideck's shortcut
   registry json used by the launcher's gamescope-window glue.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..config import UbisoftConfig

logger = logging.getLogger(__name__)
_INSTALLS_REG_SECTION_FMT = (
    '[Software\\\\WOW6432Node\\\\Ubisoft\\\\Launcher\\\\Installs\\\\{install_id}]'
)
_STEAM_COMPAT_ENV_VARS = ('SteamAppId', 'SteamGameId', 'STEAM_COMPAT_APP_ID')


def resolve_active_prefix_dir(prefix_path: str) -> str | None:
    """Resolve active prefix dir."""
    for candidate in (
        os.path.join(prefix_path, 'pfx'),
        prefix_path,
    ):
        if os.path.isfile(os.path.join(candidate, 'system.reg')):
            return candidate
    return None


def read_system_reg(active_prefix: str) -> tuple[str, str] | None:
    """Read system reg."""
    reg_path = os.path.join(active_prefix, 'system.reg')
    try:
        with open(reg_path, encoding='utf-8', errors='replace') as f:
            return f.read(), reg_path
    except OSError as e:
        logger.debug('[Ubisoft.registry] reg read: %s', e)
        return None


def find_install_registry_section_bounds(
    content: str, section: str,
) -> tuple[int, int] | None:
    """Find install registry section bounds."""
    start = content.find(section)
    if start < 0:
        return None
    nxt = content.find('\n[', start + len(section))
    end = nxt if nxt >= 0 else len(content)
    return start, end


def _update_or_append_install_section(
    content: str, section: str, values: list[str],
) -> str:
    """Update or append install section."""
    bounds = find_install_registry_section_bounds(content, section)
    body = section + '\n' + '\n'.join(values) + '\n'
    if bounds is None:
        sep = '\n' if content and not content.endswith('\n') else ''
        return content + sep + body
    start, end = bounds
    return content[:start] + body + content[end:]


def inject_install_registry(
    prefix_path: str, install_id: str, install_dir: str,
) -> None:
    """Inject install registry."""
    active = resolve_active_prefix_dir(prefix_path)
    if active is None:
        return
    pair = read_system_reg(active)
    if pair is None:
        return
    content, reg_path = pair
    section = _INSTALLS_REG_SECTION_FMT.format(install_id=install_id)
    install_dir_wine = 'Z:' + install_dir.replace('/', '\\\\')
    values = [f'"InstallDir"="{install_dir_wine}"']
    new_content = _update_or_append_install_section(content, section, values)
    if new_content == content:
        return
    try:
        with open(reg_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except OSError as e:
        logger.warning('[Ubisoft.registry] reg write: %s', e)


def clean_install_registry(prefix_path: str, install_id: str) -> None:
    """Clean install registry."""
    active = resolve_active_prefix_dir(prefix_path)
    if active is None:
        return
    pair = read_system_reg(active)
    if pair is None:
        return
    content, reg_path = pair
    section = _INSTALLS_REG_SECTION_FMT.format(install_id=install_id)
    bounds = find_install_registry_section_bounds(content, section)
    if bounds is None:
        return
    start, end = bounds
    new_content = content[:start] + content[end:]
    try:
        with open(reg_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except OSError as e:
        logger.warning('[Ubisoft.registry] reg write: %s', e)


def get_directory_size(path: str) -> int:
    """Get directory size."""
    if not os.path.isdir(path):
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return total


def parse_positive_int(value: Any) -> int | None:
    """Parse positive int."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class _ShortcutRegistry:
    """Shortcut registry."""

    def __init__(self, config: UbisoftConfig) -> None:
        """Initialize the instance."""
        self._config = config
        self._registry_path = os.path.join(
            config.data_dir_expanded, 'shortcuts_registry.json',
        )

    def load(self) -> dict[str, Any]:
        """Load."""
        if not os.path.isfile(self._registry_path):
            return {}
        try:
            with open(self._registry_path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def scan_for_ubisoft_appid(self, registry: dict[str, Any]) -> int | None:
        """Scan for UBISOFT appid."""
        for key, entry in registry.items():
            if not isinstance(key, str) or not key.startswith('ubisoft:'):
                continue
            if not isinstance(entry, dict):
                continue
            appid = parse_positive_int(entry.get('appid_unsigned'))
            if appid:
                return appid
        return None

    def resolve_shortcut_appid(
        self, store_game_id: str | None,
    ) -> int | None:
        """Resolve shortcut appid."""
        registry = self.load()
        if store_game_id:
            entry = registry.get(store_game_id)
            if isinstance(entry, dict):
                appid = parse_positive_int(entry.get('appid_unsigned'))
                if appid:
                    return appid
        return self.scan_for_ubisoft_appid(registry)
