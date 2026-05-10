"""propagator.py — Spread credentials across every Ubisoft prefix.

# OP-60d | py_modules/unifideck/stores/ubisoft/session/propagator.py | Depends: (none)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import UbisoftConfig
    from .payload import _PayloadSync
    from .reader import _CredentialReader

logger = logging.getLogger(__name__)


class _CredentialPropagator:
    """Credential propagator."""

    def __init__(
        self, *,
        config: UbisoftConfig,
        payload: _PayloadSync,
        reader: _CredentialReader,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._payload = payload
        self._reader = reader

    def propagate_credentials_to_all(self) -> int:
        """Propagate credentials to all."""
        source = self._reader.find_best_credential_source()
        if not source:
            return 0
        copied = 0
        for target in self._all_prefixes():
            if target == source:
                continue
            copied += self._payload.sync_credentials_to_prefix(source, target)
        return copied

    def propagate_auth_artifacts_to_all(self) -> int:
        """Propagate auth artifacts to all."""
        source = self._reader.find_best_credential_source()
        if not source:
            return 0
        copied = 0
        for target in self._all_prefixes():
            if target == source:
                continue
            copied += self._payload.sync_auth_artifacts_to_prefix(
                source, target,
            )
        return copied

    def propagate_all_to_all(self) -> None:
        """Propagate all to all."""
        creds = self.propagate_credentials_to_all()
        artifacts = self.propagate_auth_artifacts_to_all()
        logger.info(
            '[Ubisoft.session] propagation: %d creds, %d artifacts',
            creds, artifacts,
        )

    def inject_into_prefix(self, prefix_path: str) -> bool:
        """Inject into prefix."""
        source = self._reader.find_best_credential_source()
        if not source or source == prefix_path:
            return False
        copied = self._payload.sync_credentials_to_prefix(source, prefix_path)
        copied += self._payload.sync_auth_artifacts_to_prefix(
            source, prefix_path,
        )
        return copied > 0

    def ensure_auth_state_in_prefixes(self, prefix_paths: list[str]) -> int:
        """Ensure auth state in prefixes."""
        injected = 0
        for prefix in prefix_paths:
            if self.inject_into_prefix(prefix):
                injected += 1
        return injected

    def retroactive_sync(self) -> dict[str, Any]:
        """Retroactive sync."""
        creds = self.propagate_credentials_to_all()
        artifacts = self.propagate_auth_artifacts_to_all()
        return {
            'credentials_copied': creds,
            'artifacts_copied': artifacts,
            'success': True,
        }

    def _all_prefixes(self) -> list[str]:
        """All prefixes."""
        prefixes = list(self._config.iter_game_prefix_paths())
        auth = self._config.auth_prefix_dir_expanded
        if auth and auth not in prefixes:
            prefixes.append(auth)
        return prefixes
