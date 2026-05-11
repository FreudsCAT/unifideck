"""
Session propagator — push auth state to every active game prefix.

OP-60d | py_modules/unifideck/stores/ubisoft/session/propagator.py

After a successful sign-in (or sign-out), the auth state in the auth
prefix needs to be reflected in every per-game prefix the user has.
``_SessionPropagator`` is the orchestration class for this:

* on sign-in: walk every game prefix, sync credentials + auth cache
  from the auth prefix;
* on sign-out: walk every game prefix, wipe credentials + auth cache.

The propagation runs in the background as an async task so the user
doesn't see a sign-in latency proportional to the number of installed
games.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import UbisoftConfig
    from .payload import _PayloadSync
    from .reader import _CredentialReader
logger = logging.getLogger(__name__)


class _CredentialPropagator:
    """Credential propagator."""

    def __init__(
        self,
        *,
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
            logger.info(
                "[UbisoftSession] no credential source for propagation",
            )
            return 0
        total = 0
        for prefix_path in self._config.iter_game_prefix_paths():
            try:
                total += self._payload.sync_credentials_to_prefix(
                    source,
                    prefix_path,
                )
            except Exception as e:
                logger.warning(
                    "[UbisoftSession] credential propagation failed for %s: %s",
                    Path(prefix_path).name,
                    e,
                )
        if total:
            logger.info(
                "[UbisoftSession] propagated %d credential file(s) across prefixes",
                total,
            )
        return total

    def propagate_auth_artifacts_to_all(self) -> int:
        """Propagate auth artifacts to all."""
        source = self._reader.find_best_credential_source()
        if not source:
            return 0
        total = 0
        for prefix_path in self._config.iter_game_prefix_paths():
            try:
                total += self._payload.sync_auth_artifacts_to_prefix(
                    source,
                    prefix_path,
                )
            except Exception as e:
                logger.warning(
                    "[UbisoftSession] artifact propagation failed for %s: %s",
                    Path(prefix_path).name,
                    e,
                )
        if total:
            logger.info(
                "[UbisoftSession] propagated %d auth cache artifact(s) across prefixes",
                total,
            )
        return total

    def propagate_all_to_all(self) -> None:
        """Propagate all to all."""
        self.propagate_credentials_to_all()
        self.propagate_auth_artifacts_to_all()

    def inject_into_prefix(self, prefix_path: str) -> bool:
        """Inject into prefix."""
        source = self._reader.find_best_credential_source()
        if not source:
            logger.warning(
                "[UbisoftSession] inject_into_prefix: no credential source found",
            )
            return False
        credentials_synced = False
        try:
            synced = self._payload.sync_credentials_to_prefix(
                source,
                prefix_path,
            )
            artifact_synced = self._payload.sync_auth_artifacts_to_prefix(
                source,
                prefix_path,
            )
            if synced:
                logger.info(
                    "[UbisoftSession] inject: synced %d credential file(s)",
                    synced,
                )
                credentials_synced = True
            if artifact_synced:
                logger.info(
                    "[UbisoftSession] inject: synced %d artifact(s)",
                    artifact_synced,
                )
                credentials_synced = True
        except Exception as e:
            logger.warning(
                "[UbisoftSession] inject auth sync failed: %s",
                e,
            )
        if not credentials_synced:
            logger.warning(
                "[UbisoftSession] inject: nothing synced",
            )
        return credentials_synced

    def ensure_auth_state_in_prefixes(
        self,
        prefix_paths: list[str],
    ) -> int:
        """Ensure auth state in prefixes."""
        ensured = 0
        for prefix_path in prefix_paths:
            if not Path(prefix_path).is_dir():
                continue
            try:
                if self.inject_into_prefix(prefix_path):
                    ensured += 1
            except Exception as e:
                logger.warning(
                    "[UbisoftSession] ensure auth state failed for %s: %s",
                    Path(prefix_path).name,
                    e,
                )
        if ensured:
            logger.info(
                "[UbisoftSession] ensured auth state across %d prefix(es)",
                ensured,
            )
        return ensured

    def retroactive_sync(self) -> dict[str, Any]:
        """Retroactive sync."""
        try:
            cred_count = self.propagate_credentials_to_all()
            self.propagate_auth_artifacts_to_all()
            target_prefixes: list[str] = []
            for hidden_prefix in (
                self._config.auth_prefix_dir_expanded,
                self._config.template_dir_expanded,
            ):
                if Path(hidden_prefix).is_dir():
                    target_prefixes.append(hidden_prefix)
            target_prefixes.extend(
                self._config.iter_game_prefix_paths(),
            )
            token_count = self.ensure_auth_state_in_prefixes(
                target_prefixes,
            )
            return {
                "success": True,
                "credentials_synced": cred_count,
                "token_propagated": token_count > 0,
            }
        except Exception as e:
            logger.error(
                "[UbisoftSession] retroactive sync failed: %s",
                e,
            )
            return {"success": False, "error": str(e)}
