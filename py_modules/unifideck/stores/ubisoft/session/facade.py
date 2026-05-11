"""facade.py — Public ``UbisoftSession`` surface.

# OP-60a | py_modules/unifideck/stores/ubisoft/session/facade.py | Depends: (none)
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from ..config import UbisoftConfig
from ..paths import UbisoftPrefixPaths
from .payload import _PayloadSync
from .propagator import _CredentialPropagator
from .reader import _CredentialReader

logger = logging.getLogger(__name__)
_CAPTURE_SENTINEL = 'credentials_captured'


class UbisoftSession:
    """Ubisoft session."""

    def __init__(
        self,
        config: UbisoftConfig,
        paths: UbisoftPrefixPaths,
        read_machine_guid: Callable[[str], str],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._paths = paths
        self._read_machine_guid = read_machine_guid
        self._reader = _CredentialReader(config=config, paths=paths)
        self._payload = _PayloadSync(self)
        self._propagator = _CredentialPropagator(
            config=config, payload=self._payload, reader=self._reader,
        )

    def has_valid_credentials(self, prefix_path: str) -> bool:
        """Check whether valid credentials."""
        return self._reader.has_valid_credentials(prefix_path)

    def get_credential_mtime(self, prefix_path: str) -> float:
        """Get credential mtime."""
        return self._reader.get_credential_mtime(prefix_path)

    def find_best_credential_source(self) -> str | None:
        """Find best credential source."""
        return self._reader.find_best_credential_source()

    def _is_valid_css(self, css_path: str, min_size: int) -> bool:
        """Is valid CSS."""
        return _CredentialReader._is_valid_css(css_path, min_size)

    def propagate_credentials_to_all(self) -> int:
        """Propagate credentials to all."""
        return self._propagator.propagate_credentials_to_all()

    def propagate_auth_artifacts_to_all(self) -> int:
        """Propagate auth artifacts to all."""
        return self._propagator.propagate_auth_artifacts_to_all()

    def propagate_all_to_all(self) -> None:
        """Propagate all to all."""
        self._propagator.propagate_all_to_all()

    def inject_into_prefix(self, prefix_path: str) -> bool:
        """Inject into prefix."""
        return self._propagator.inject_into_prefix(prefix_path)

    def ensure_auth_state_in_prefixes(self, prefix_paths: list[str]) -> int:
        """Ensure auth state in prefixes."""
        return self._propagator.ensure_auth_state_in_prefixes(prefix_paths)

    def retroactive_sync(self) -> dict[str, Any]:
        """Retroactive sync."""
        return self._propagator.retroactive_sync()

    def capture(self, prefix_path: str) -> str | None:
        """Capture."""
        if not self.has_valid_credentials(prefix_path):
            return None
        try:
            mtime = self.get_credential_mtime(prefix_path)
            self._write_stored_mtime(mtime)
        except OSError as e:
            logger.warning('[Ubisoft.session] capture mtime write: %s', e)
        return _CAPTURE_SENTINEL

    def _read_stored_mtime(self) -> float:
        """Read stored mtime."""
        path = self._config.upc_session_file_expanded
        try:
            with open(path, encoding='utf-8') as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return 0.0

    def _write_stored_mtime(self, mtime: float) -> None:
        """Write stored mtime."""
        path = self._config.upc_session_file_expanded
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'{mtime:.6f}')

    def clear_session_file(self) -> None:
        """Clear session file."""
        path = self._config.upc_session_file_expanded
        try:
            if os.path.isfile(path):
                os.unlink(path)
        except OSError as e:
            logger.warning('[Ubisoft.session] clear failed: %s', e)
