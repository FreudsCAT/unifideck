"""
Session reader — extract auth state from a Wine prefix.

OP-60c | py_modules/unifideck/stores/ubisoft/session/reader.py

``_SessionReader`` reads UPC's authenticated state out of a Wine prefix:

* the credential vault files (DPAPI-encrypted);
* the auth cache (cookies, tokens, machine GUID);
* the validation timestamp;
* the signed-in user's display name (parsed from ``ownership``).

The reader is read-only — propagation happens through ``payload.py``.
The split between reader and payload exists so the same parsed session
can be propagated to multiple target prefixes without re-reading.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .payload import _CSS_MIN_SOURCE_SIZE

if TYPE_CHECKING:
    from unifideck.stores.ubisoft.config import UbisoftConfig
    from unifideck.stores.ubisoft.paths import UbisoftPrefixPaths
_CSS_MIN_VALID_SIZE = 100
logger = logging.getLogger(__name__)


class _CredentialReader:
    """Credential reader."""

    def __init__(
        self,
        *,
        config: UbisoftConfig,
        paths: UbisoftPrefixPaths,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths

    def has_valid_credentials(self, prefix_path: str) -> bool:
        """Check whether valid credentials."""
        for _root, user_home in self._paths.iter_user_homes(
            prefix_path,
            pfx_first=True,
        ):
            css = self._css_path(user_home)
            if self._is_valid_css(css, _CSS_MIN_VALID_SIZE):
                return True
        return False

    def get_credential_mtime(self, prefix_path: str) -> float:
        """Get credential mtime."""
        best: float = 0.0
        for _root, user_home in self._paths.iter_user_homes(
            prefix_path,
            pfx_first=True,
        ):
            css = self._css_path(user_home)
            if not self._is_valid_css(css, _CSS_MIN_VALID_SIZE):
                continue
            try:
                mtime = Path(css).stat().st_mtime
            except OSError:
                continue
            if mtime > best:
                best = mtime
        return best

    def get_credential_size(self, prefix_path: str) -> int:
        """Largest valid ``ConnectSecureStorage.dat`` size in the prefix, or 0.

        UPC's credential file shrinks when a session logs out (the token is
        stripped). Comparing a source prefix's size against the auth prefix's
        lets the capture path recognise a logged-out / stale source and refuse
        to propagate it — protecting both the auth prefix and the template.
        """
        best = 0
        for _root, user_home in self._paths.iter_user_homes(
            prefix_path,
            pfx_first=True,
        ):
            css = self._css_path(user_home)
            if not self._is_valid_css(css, _CSS_MIN_VALID_SIZE):
                continue
            try:
                size = Path(css).stat().st_size
            except OSError:
                continue
            if size > best:
                best = size
        return best

    def find_best_credential_source(self) -> str | None:
        """Find best credential source."""
        auth_source = self._check_auth_prefix_for_credentials()
        if auth_source:
            return auth_source
        return self._find_freshest_game_prefix_credentials()

    def _check_auth_prefix_for_credentials(self) -> str | None:
        """Check auth prefix for credentials."""
        auth_dir = self._config.auth_prefix_dir_expanded
        if not Path(auth_dir).is_dir():
            return None
        for _root, user_home in self._paths.iter_user_homes(
            auth_dir,
            pfx_first=True,
        ):
            css = self._css_path(user_home)
            if self._is_valid_css(css, _CSS_MIN_SOURCE_SIZE):
                return auth_dir
        return None

    def _find_freshest_game_prefix_credentials(
        self,
    ) -> str | None:
        """Find freshest game prefix credentials."""
        prefixes_dir = self._config.prefixes_dir_expanded
        prefixes_p = Path(prefixes_dir)
        if not prefixes_p.is_dir():
            return None
        try:
            entries = list(prefixes_p.iterdir())
        except OSError:
            return None
        best_mtime: float = 0.0
        best_prefix: str | None = None
        for entry in entries:
            if not entry.is_dir():
                continue
            prefix = str(entry)
            mtime = self._best_css_mtime_for_prefix(prefix)
            if mtime is not None and mtime > best_mtime:
                best_mtime = mtime
                best_prefix = prefix
        return best_prefix

    def _best_css_mtime_for_prefix(
        self,
        prefix: str,
    ) -> float | None:
        """Best CSS mtime for prefix."""
        for _root, user_home in self._paths.iter_user_homes(
            prefix,
            pfx_first=True,
        ):
            css = self._css_path(user_home)
            if not self._is_valid_css(css, _CSS_MIN_SOURCE_SIZE):
                continue
            try:
                return Path(css).stat().st_mtime
            except OSError:
                continue
        return None

    def _css_path(self, user_home: str) -> str:
        """Css path."""
        return str(
            Path(user_home) / self._config.upc_local_subdir / "ConnectSecureStorage.dat"
        )

    @staticmethod
    def _is_valid_css(css_path: str, min_size: int) -> bool:
        """Is valid CSS."""
        css_p = Path(css_path)
        if not css_p.is_file():
            return False
        try:
            return css_p.stat().st_size > min_size
        except OSError:
            return False
