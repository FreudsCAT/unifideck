"""
UPC session facade — propagate auth state across Wine prefixes.

OP-60a | py_modules/unifideck/stores/ubisoft/session/facade.py

UPC stores its auth state (credentials, refresh tokens, machine GUID)
inside the Wine prefix where the user signed in. To launch games from
other prefixes we have to copy that state into each prefix on demand.

``UbisoftSession`` is the orchestration class for this propagation. It
delegates to:

* ``reader.py`` (OP-60c) — read sessions out of the auth prefix;
* ``payload.py`` (OP-60b) — copy credentials/artifacts to target prefixes;
* ``propagator.py`` (OP-60d) — orchestrate propagation across multiple
  game prefixes when the auth state changes.

The session facade exposes the ``_read_machine_guid`` helper used by
the payload module's DPAPI-guard logic to refuse copying credentials
into a prefix with a different machine GUID (would corrupt the DPAPI
key vault).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from unifideck.stores.ubisoft.config import UbisoftConfig
from unifideck.stores.ubisoft.paths import UbisoftPrefixPaths

from .payload import _PayloadSync
from .propagator import _CredentialPropagator
from .reader import _CredentialReader

logger = logging.getLogger(__name__)
_CAPTURE_SENTINEL = "credentials_captured"


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
        self._reader = _CredentialReader(
            config=config,
            paths=paths,
        )
        self._payload = _PayloadSync(self)
        self._propagator = _CredentialPropagator(
            config=config,
            paths=paths,
            payload=self._payload,
            reader=self._reader,
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
        return self._reader._is_valid_css(css_path, min_size)

    def propagate_credentials_to_all(self) -> int:
        """Propagate credentials to all."""
        return self._propagator.propagate_credentials_to_all()

    def propagate_auth_artifacts_to_all(self) -> int:
        """Propagate auth artifacts to all."""
        return self._propagator.propagate_auth_artifacts_to_all()

    def propagate_all_to_all(self) -> None:
        """Propagate all to all."""
        self._propagator.propagate_all_to_all()

    def purge_credentials_from_all(self) -> int:
        """Purge credentials from all game prefixes + the template."""
        return self._propagator.purge_credentials_from_all()

    def inject_into_prefix(self, prefix_path: str) -> bool:
        """Inject into prefix."""
        return self._propagator.inject_into_prefix(prefix_path)

    def ensure_auth_state_in_prefixes(
        self,
        prefix_paths: list[str],
    ) -> int:
        """Ensure auth state in prefixes."""
        return self._propagator.ensure_auth_state_in_prefixes(
            prefix_paths,
        )

    def retroactive_sync(self) -> dict[str, Any]:
        """Retroactive sync."""
        return self._propagator.retroactive_sync()

    def capture(self, prefix_path: str) -> str | None:
        """Capture."""
        if not self._reader.has_valid_credentials(prefix_path):
            return None
        auth_dir = self._config.auth_prefix_dir_expanded
        source_is_auth = (
            Path(prefix_path).resolve() == Path(auth_dir).resolve()
        )
        # Never propagate a regressed (logged-out) credential. UPC's
        # ``ConnectSecureStorage.dat`` shrinks on logout; a game/launch/uninstall
        # capture whose source is SMALLER than the live auth credential is a
        # logout/stale token — skip the whole capture so neither the credential
        # nor the (unguarded) auth-cache artifacts can carry it into the auth
        # prefix or the template. (The auth prefix itself is exempt: it is the
        # source of truth and may legitimately shrink on the user's own logout.)
        if not source_is_auth and self._source_is_logged_out(
            prefix_path, auth_dir,
        ):
            logger.info(
                "[UbisoftSession] capture: %s is logged-out/stale relative to "
                "the auth prefix — skipping (template + auth left intact)",
                Path(prefix_path).name,
            )
            return None
        new_mtime = self._reader.get_credential_mtime(prefix_path)
        if not new_mtime:
            return None
        stored_mtime = self._read_stored_mtime()
        credentials_changed = new_mtime > stored_mtime
        if credentials_changed:
            self._write_stored_mtime(new_mtime)
            logger.info(
                "[UbisoftSession] detected new UPC "
                "credentials (ConnectSecureStorage.dat)",
            )
        # Capture-back targets the AUTH prefix only. The template is a pristine
        # golden image that must change ONLY on an explicit sign-in or sign-out
        # — so a game/launch/uninstall capture must never write it. The one
        # exception is the sign-in path (``session_monitor`` captures from the
        # auth prefix): there the auth→auth copy self-skips and auth→template is
        # the legitimate sign-in hydration. Cloned per-game prefixes re-inject
        # from auth, so auth alone is a sufficient fresh source.
        targets = (
            (auth_dir, self._config.template_dir_expanded)
            if source_is_auth
            else (auth_dir,)
        )
        for target in targets:
            if not Path(target).is_dir():
                continue
            if Path(target).resolve() == Path(prefix_path).resolve():
                continue
            try:
                self._payload.sync_credentials_to_prefix(
                    prefix_path,
                    target,
                )
                self._payload.sync_auth_artifacts_to_prefix(
                    prefix_path,
                    target,
                )
            except Exception as e:
                logger.warning(
                    "[UbisoftSession] capture sync to %s failed: %s",
                    Path(target).name,
                    e,
                )
        if credentials_changed:
            logger.info(
                "[UbisoftSession] captured credentials → auth prefix updated",
            )
            return _CAPTURE_SENTINEL
        return None

    def stored_credential_was_rejected(self, prefix_path: str) -> bool:
        """True when UPC signed the user OUT of a prefix we had signed IN.

        We inject the auth prefix's credential into every game prefix before
        running UPC. If UPC then leaves that prefix holding a logged-out
        credential, UPC did not accept what we gave it — the stored token is
        dead server-side (Ubisoft rotates and invalidates; see the uninstall
        capture-back note). No local copy can revive it, and because
        ``capture`` correctly refuses to overwrite a "logged-in" credential
        with a smaller one, nothing else notices: every later install injects
        the same dead token and the user is asked to sign in forever.

        Deliberately read-only — it reports, it does not purge. Clearing a
        user's credentials is their call (QAM → Ubisoft → Sign out), not a
        side effect of a heuristic.

        Confirmed live 2026-08-01: after a prefix reset forced UPC to run
        credential-less, the Aug-1 04:55 auth credential stopped working;
        every subsequent install injected it and prompted for sign-in.
        """
        auth_dir = self._config.auth_prefix_dir_expanded
        if not self._reader.has_valid_credentials(auth_dir):
            return False  # nothing stored → a sign-in prompt is expected
        return self._source_is_logged_out(prefix_path, auth_dir)

    def _source_is_logged_out(self, prefix_path: str, auth_dir: str) -> bool:
        """True if ``prefix_path``'s credential is smaller than auth's.

        The logout signature (see :meth:`_CredentialReader.get_credential_size`).
        Returns False when either side has no readable credential, so a first
        capture that seeds an empty auth prefix still flows.
        """
        src = self._reader.get_credential_size(prefix_path)
        auth = self._reader.get_credential_size(auth_dir)
        return bool(auth and src and src < auth)

    def _read_stored_mtime(self) -> float:
        """Read stored mtime."""
        session_file = self._config.upc_session_file_expanded
        if not Path(session_file).is_file():
            return 0.0
        try:
            content = (
                Path(session_file)
                .read_text(
                    encoding="utf-8",
                )
                .strip()
            )
        except OSError:
            return 0.0
        if not content.startswith("credential_mtime:"):
            return 0.0
        try:
            return float(content.split(":", 1)[1])
        except (ValueError, IndexError):
            return 0.0

    def _write_stored_mtime(self, mtime: float) -> None:
        """Write stored mtime."""
        session_file = self._config.upc_session_file_expanded
        try:
            Path(self._config.data_dir_expanded).mkdir(
                parents=True,
                exist_ok=True,
            )
            Path(session_file).write_text(
                f"credential_mtime:{mtime}\n",
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(
                "[UbisoftSession] could not write mtime marker: %s",
                e,
            )

    def clear_session_file(self) -> None:
        """Clear session file."""
        session_file = self._config.upc_session_file_expanded
        if not Path(session_file).is_file():
            return
        try:
            Path(session_file).unlink()
            logger.info(
                "[UbisoftSession] removed UPC session marker",
            )
        except OSError as e:
            logger.warning(
                "[UbisoftSession] could not remove session marker: %s",
                e,
            )
