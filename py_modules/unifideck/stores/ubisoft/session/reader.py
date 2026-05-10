"""reader.py — Locate the freshest UPC credential cache.

# OP-60c | py_modules/unifideck/stores/ubisoft/session/reader.py | Depends: (none)
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import UbisoftConfig
    from ..paths import UbisoftPrefixPaths

_CSS_MIN_VALID_SIZE = 100
logger = logging.getLogger(__name__)


class _CredentialReader:
    """Credential reader."""

    def __init__(
        self, *, config: UbisoftConfig, paths: UbisoftPrefixPaths,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths

    def has_valid_credentials(self, prefix_path: str) -> bool:
        """Check whether valid credentials."""
        for _root, user_home in self._paths.iter_user_homes(
            prefix_path, pfx_first=True,
        ):
            css = self._css_path(user_home)
            if self._is_valid_css(css, _CSS_MIN_VALID_SIZE):
                return True
        return False

    def get_credential_mtime(self, prefix_path: str) -> float:
        """Get credential mtime."""
        best = self._best_css_mtime_for_prefix(prefix_path)
        return best if best is not None else 0.0

    def find_best_credential_source(self) -> str | None:
        """Find best credential source."""
        candidates: list[tuple[float, str]] = []
        for prefix_path in (
            self._config.iter_game_prefix_paths()
            + [self._config.auth_prefix_dir_expanded]
        ):
            if not os.path.isdir(prefix_path):
                continue
            mtime = self._best_css_mtime_for_prefix(prefix_path)
            if mtime is not None:
                candidates.append((mtime, prefix_path))
        if not candidates:
            best_auth = self._check_auth_prefix_for_credentials()
            return best_auth
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _check_auth_prefix_for_credentials(self) -> str | None:
        """Check auth prefix for credentials."""
        auth_prefix = self._config.auth_prefix_dir_expanded
        if not os.path.isdir(auth_prefix):
            return None
        if self.has_valid_credentials(auth_prefix):
            return auth_prefix
        return None

    def _find_freshest_game_prefix_credentials(self) -> str | None:
        """Find freshest game prefix credentials."""
        best: tuple[float, str] | None = None
        for prefix_path in self._config.iter_game_prefix_paths():
            mtime = self._best_css_mtime_for_prefix(prefix_path)
            if mtime is None:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, prefix_path)
        return best[1] if best else None

    def _best_css_mtime_for_prefix(self, prefix: str) -> float | None:
        """Best CSS mtime for prefix."""
        best: float | None = None
        for _root, user_home in self._paths.iter_user_homes(
            prefix, pfx_first=True,
        ):
            css = self._css_path(user_home)
            if not self._is_valid_css(css, _CSS_MIN_VALID_SIZE):
                continue
            try:
                mtime = os.path.getmtime(css)
            except OSError:
                continue
            if best is None or mtime > best:
                best = mtime
        return best

    def _css_path(self, user_home: str) -> str:
        """CSS path."""
        return os.path.join(
            user_home,
            self._config.upc_local_subdir,
            'ConnectSecureStorage.dat',
        )

    @staticmethod
    def _is_valid_css(css_path: str, min_size: int) -> bool:
        """Check whether valid CSS."""
        try:
            return (
                os.path.isfile(css_path)
                and os.path.getsize(css_path) >= min_size
            )
        except OSError:
            return False
