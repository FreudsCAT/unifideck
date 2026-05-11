"""shortcut.py — Manage the "Ubisoft Connect" Steam shortcut.

# OP-58c | py_modules/unifideck/stores/ubisoft/auth/shortcut.py | Depends: (none)

The auth flow leans on a Steam shortcut so UPC opens inside gamescope
with the right Proton runtime. This module owns the shortcut's
creation, validation, and pruning of stale variants left over from
older plugin builds.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....services.shortcut import ShortcutService
    from .facade import UbisoftAuth

logger = logging.getLogger(__name__)
_AUTH_LAUNCH_OPTIONS_TEMPLATE = (
    '{store_id} UNIFIDECK_UBISOFT_ACTION=auth '
    'UNIFIDECK_UBISOFT_PREFIX_NAME={prefix_name}'
)
_AUTH_SHORTCUT_NAME = 'Ubisoft Connect'
_LEGACY_AUTH_LAUNCH_OPTIONS = 'ubisoft:.template'
_ORPHAN_SHORTCUT_NAMES = frozenset({'upc.exe', 'ubisoft connect'})


def _prune_orphan_shortcuts(shortcuts: dict[str, Any]) -> int:
    """Prune orphan shortcuts.

    Removes entries whose AppName matches a known orphan and whose
    LaunchOptions don't carry a current Unifideck launch token.
    """
    removed = 0
    for key in list(shortcuts.keys()):
        entry = shortcuts.get(key)
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('AppName') or entry.get('appname') or '').strip().lower()
        if name not in _ORPHAN_SHORTCUT_NAMES:
            continue
        opts = str(entry.get('LaunchOptions') or '')
        if 'UNIFIDECK_UBISOFT_ACTION' in opts:
            continue
        del shortcuts[key]
        removed += 1
    return removed


def _prune_legacy_template_shortcuts(shortcuts: dict[str, Any]) -> int:
    """Prune legacy template shortcuts (ubisoft:.template store_id)."""
    removed = 0
    for key in list(shortcuts.keys()):
        entry = shortcuts.get(key)
        if not isinstance(entry, dict):
            continue
        opts = str(entry.get('LaunchOptions') or '')
        if _LEGACY_AUTH_LAUNCH_OPTIONS in opts:
            del shortcuts[key]
            removed += 1
    return removed


class _AuthShortcut:
    """Auth shortcut."""

    def __init__(self, parent: UbisoftAuth) -> None:
        """Initialize the instance."""
        self._parent = parent

    def get_launcher_path(self) -> str:
        """Get launcher path."""
        config = self._parent._state.config
        return str(
            Path(config.auth_prefix_dir_expanded)
            / config.upc_connect_relative_path
        )

    def build_auth_launch_options(self) -> str:
        """Build auth launch options."""
        config = self._parent._state.config
        return _AUTH_LAUNCH_OPTIONS_TEMPLATE.format(
            store_id=config.auth_shortcut_store_id,
            prefix_name=config.auth_prefix_name,
        )

    async def ensure_auth_shortcut(self) -> int | None:
        """Ensure auth shortcut."""
        sm = self._parent._services.shortcut_service
        if sm is None:
            return None
        config = self._parent._state.config
        existing = await self.try_existing_shortcut(
            sm, config.auth_shortcut_store_id,
        )
        if existing is not None:
            return existing
        return await self.create_new_auth_shortcut(
            sm, config.auth_shortcut_store_id,
        )

    async def try_existing_shortcut(
        self, sm: ShortcutService, store_id: str,
    ) -> int | None:
        """Try existing shortcut."""
        try:
            registry = await sm.get_registry()
        except Exception:
            return None
        entry = registry.get(store_id)
        if not isinstance(entry, dict):
            return None
        appid = entry.get('appid_unsigned') or entry.get('appid')
        try:
            return int(appid) if appid is not None else None
        except (TypeError, ValueError):
            return None

    async def create_new_auth_shortcut(
        self, sm: ShortcutService, store_id: str,
    ) -> int | None:
        """Create new auth shortcut."""
        try:
            appid = await sm.create_shortcut(
                name=_AUTH_SHORTCUT_NAME,
                exe=self.get_launcher_path(),
                start_dir=str(Path(self.get_launcher_path()).parent),
                launch_options=self.build_auth_launch_options(),
                store_id=store_id,
            )
        except Exception as e:
            logger.warning(
                '[Ubisoft.auth] create_shortcut failed: %s', e,
            )
            return None
        unsigned = self._coerce_int(appid)
        if unsigned is None:
            return None
        await self._finalize_new_shortcut(sm, appid=unsigned, unsigned_id=unsigned)
        return unsigned

    async def _finalize_new_shortcut(
        self, sm: ShortcutService, appid: int, unsigned_id: int,
    ) -> None:
        """Finalize new shortcut."""
        try:
            await self._parent._fetch_auth_shortcut_artwork(unsigned_id, force=True)
        except Exception as e:
            logger.debug('[Ubisoft.auth] artwork fetch failed: %s', e)
        await self._parent._register_shortcut(sm, appid, _AUTH_SHORTCUT_NAME)

    def _add_canonical_if_missing(
        self,
        shortcuts: dict[str, Any],
        launcher_path: str,
        appid: int,
        unsigned_id: int,
    ) -> bool:
        """Add canonical if missing."""
        for entry in shortcuts.values():
            if not isinstance(entry, dict):
                continue
            if entry.get('appid') == appid or entry.get('appid_unsigned') == unsigned_id:
                return False
        new_key = str(len(shortcuts))
        shortcuts[new_key] = {
            'appid': appid,
            'appid_unsigned': unsigned_id,
            'AppName': _AUTH_SHORTCUT_NAME,
            'Exe': launcher_path,
            'StartDir': str(Path(launcher_path).parent),
            'LaunchOptions': self.build_auth_launch_options(),
        }
        return True

    async def validate_auth_shortcut(self, sm: ShortcutService) -> bool:
        """Validate auth shortcut."""
        try:
            shortcuts = await sm.get_vdf_shortcuts()
        except Exception:
            return False
        config = self._parent._state.config
        expected_opts = self.build_auth_launch_options()
        for entry in shortcuts.values() if isinstance(shortcuts, dict) else []:
            if not isinstance(entry, dict):
                continue
            if config.auth_shortcut_store_id in str(entry.get('LaunchOptions') or ''):
                return self._fix_shortcut_fields(
                    entry, self.get_launcher_path(),
                    expected_opts,
                    self._coerce_int(entry.get('appid')) or 0,
                )
        return False

    def _fix_shortcut_fields(
        self, entry: dict[str, Any], launcher_path: str,
        expected_launch_options: str, expected_appid: int,
    ) -> bool:
        """Fix shortcut fields."""
        changed = False
        if entry.get('Exe') != launcher_path:
            entry['Exe'] = launcher_path
            changed = True
        if entry.get('LaunchOptions') != expected_launch_options:
            entry['LaunchOptions'] = expected_launch_options
            changed = True
        return not changed  # True when nothing needed fixing

    async def auth_shortcut_exists_in_vdf(self) -> bool:
        """Auth shortcut exists in VDF."""
        sm = self._parent._services.shortcut_service
        if sm is None:
            return False
        try:
            shortcuts = await sm.get_vdf_shortcuts()
        except Exception:
            return False
        return self.shortcut_in_vdf(shortcuts if isinstance(shortcuts, dict) else {})

    async def add_shortcut_to_vdf(
        self, sm: ShortcutService, appid: int,
    ) -> None:
        """Add shortcut to VDF."""
        try:
            await sm.persist_shortcut(appid=appid)
        except Exception as e:
            logger.debug('[Ubisoft.auth] persist_shortcut failed: %s', e)

    def shortcut_in_vdf(self, shortcuts: dict[str, Any]) -> bool:
        """Shortcut in VDF."""
        config = self._parent._state.config
        for entry in shortcuts.values():
            if not isinstance(entry, dict):
                continue
            if config.auth_shortcut_store_id in str(entry.get('LaunchOptions') or ''):
                return True
        return False

    @staticmethod
    def extract_store_id(launch_options: str) -> str:
        """Extract store ID from a LaunchOptions string."""
        if not launch_options:
            return ''
        head = launch_options.split(' ', 1)[0]
        return head.strip()

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        """Coerce int."""
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
