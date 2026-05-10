"""paths.py — Filesystem helpers for the Ubisoft Wine prefix.

# OP-55c | py_modules/unifideck/stores/ubisoft/paths.py | Depends: (none)

UPC writes its caches and binaries to a few well-known relative paths
inside the Wine prefix. Both bare-Wine (``drive_c/...``) and Proton
(``pfx/drive_c/...``) layouts exist; this module locates files in both.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

from .config import UbisoftConfig


class UbisoftPrefixPaths:
    """Ubisoft prefix paths."""

    def __init__(self, config: UbisoftConfig) -> None:
        """Initialize the instance."""
        self._config = config

    def find_upc_exe(self, prefix_path: str) -> str | None:
        """Find UPC exe."""
        return self._first_existing(prefix_path, self._config.upc_relative_path)

    def find_connect_exe(self, prefix_path: str) -> str | None:
        """Find connect exe."""
        return self._first_existing(
            prefix_path, self._config.upc_connect_relative_path,
        )

    def find_configurations(self, prefix_path: str) -> str | None:
        """Find configurations."""
        return self._first_existing(
            prefix_path, self._config.configurations_relative_path,
        )

    def iter_user_homes(
        self, prefix_path: str, pfx_first: bool = False,
    ) -> Iterator[tuple[str, str]]:
        """Iter user homes.

        Yields (prefix_root, user_home) for every non-system user
        directory in both bare-Wine and Proton ``pfx/`` layouts.
        """
        roots = [prefix_path, os.path.join(prefix_path, "pfx")]
        if pfx_first:
            roots = list(reversed(roots))
        skip = set(self._config.wine_system_users)
        for prefix_root in roots:
            users_dir = os.path.join(prefix_root, "drive_c", "users")
            if not os.path.isdir(users_dir):
                continue
            try:
                entries = sorted(os.listdir(users_dir))
            except OSError:
                continue
            for entry in entries:
                if entry in skip:
                    continue
                user_home = os.path.join(users_dir, entry)
                if os.path.isdir(user_home):
                    yield prefix_root, user_home

    def get_prefix_path(self, space_id: str) -> str:
        """Get prefix path."""
        return os.path.join(self._config.prefixes_dir_expanded, space_id)

    @staticmethod
    def _find_in_prefix(prefix_path: str, relative: str) -> str | None:
        """Find in prefix."""
        for candidate in (
            os.path.join(prefix_path, relative),
            os.path.join(prefix_path, "pfx", relative),
        ):
            if os.path.isfile(candidate):
                return candidate
        return None

    def _first_existing(self, prefix_path: str, relative: str) -> str | None:
        """Search both bare and pfx/ layouts; return first hit."""
        return self._find_in_prefix(prefix_path, relative)
