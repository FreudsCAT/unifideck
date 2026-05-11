"""
Wine prefix path enumeration helpers.

OP-55c | py_modules/unifideck/stores/ubisoft/paths.py

``UbisoftPrefixPaths`` knows how to walk a Wine prefix and list the user
home directories inside it. Wine prefixes commonly contain multiple
"users" under ``drive_c/users/`` (e.g. ``steamuser``, ``Public``, plus
optionally per-Steam-user folders); the order in which they're visited
matters because UPC payload files are picked up from the *first* user
home that contains them.

Key method: ``iter_user_homes(prefix, pfx_first=False)`` which yields
``(root, user_home)`` tuples. The ``pfx_first`` flag is used by the
session-propagation code to ensure the prefix-default user is tried
before the Steam users — required for DPAPI credential matching.
"""

from __future__ import annotations
from collections.abc import Iterator
from pathlib import Path
from .config import UbisoftConfig


class UbisoftPrefixPaths:
    """Ubisoft prefix paths."""

    def __init__(self, config: UbisoftConfig) -> None:
        """Initialize the instance."""
        self._config = config

    def find_upc_exe(self, prefix_path: str) -> str | None:
        """Find UPC exe."""
        return self._find_in_prefix(
            prefix_path,
            self._config.upc_relative_path,
        )

    def find_connect_exe(self, prefix_path: str) -> str | None:
        """Find connect exe."""
        return self._find_in_prefix(
            prefix_path,
            self._config.upc_connect_relative_path,
        )

    def find_configurations(
        self,
        prefix_path: str,
    ) -> str | None:
        """Find configurations."""
        return self._find_in_prefix(
            prefix_path,
            self._config.configurations_relative_path,
        )

    def iter_user_homes(
        self,
        prefix_path: str,
        pfx_first: bool = False,
    ) -> Iterator[tuple[str, str]]:
        """Iter user homes."""
        roots = [
            prefix_path,
            str(Path(prefix_path) / "pfx"),
        ]
        if pfx_first:
            roots = list(reversed(roots))
        skip = set(self._config.wine_system_users)
        for prefix_root in roots:
            users_dir = Path(prefix_root) / "drive_c" / "users"
            if not users_dir.is_dir():
                continue
            try:
                entries = list(users_dir.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.name in skip:
                    continue
                if entry.is_dir():
                    yield prefix_root, str(entry)

    def get_prefix_path(self, space_id: str) -> str:
        """Get prefix path."""
        return str(
            Path(self._config.prefixes_dir_expanded) / space_id,
        )

    @staticmethod
    def _find_in_prefix(
        prefix_path: str,
        relative: str,
    ) -> str | None:
        """Find in prefix."""
        prefix = Path(prefix_path)
        for candidate in (
            prefix / relative,
            prefix / "pfx" / relative,
        ):
            if candidate.is_file() or candidate.is_dir():
                return str(candidate)
        return None
