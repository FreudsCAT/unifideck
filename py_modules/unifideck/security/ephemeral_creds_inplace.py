"""security/ephemeral_creds_inplace.py — In-place ephemeral plaintext.

Sibling primitive to :class:`EphemeralCredentialContext` (in
``ephemeral_creds.py``) for CLIs that can't accept a fresh config
directory: where the credentials file shares its directory with
other persistent state (legendary's ``installed.json`` +
``metadata/``, nile's library cache, etc.) we can't override the
entire config dir to a tempdir — that would erase the sibling
state for the duration of the call.

Instead :class:`InPlaceEphemeralFile` only touches the single
credentials file the CLI reads, decrypting it from the canonical
ciphertext path before the CLI runs and re-encrypting whatever
the CLI wrote back when the context exits.

This file was split off from ``ephemeral_creds.py`` to bring
that module under the 550-LOC volumetry gate; both primitives
remain importable from the original module via a re-export.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .ephemeral_creds import EphemeralCredentialError
from .secure_io import (
    SecureIOError,
    secure_read_bytes,
    secure_write_atomic,
)
from .secure_token_store import SecureTokenStore

logger = logging.getLogger(__name__)


class InPlaceEphemeralFile:
    """Decrypt ciphertext at a fixed plaintext path; re-encrypt on exit.

    Sibling primitive to ``EphemeralCredentialContext`` for CLIs
    whose config directory holds other persistent state besides
    credentials (legendary's ``installed.json`` + ``metadata/``,
    nile's library cache, etc.). Overriding the entire config
    dir to a fresh tempdir would erase that state for the
    duration of the call; instead this primitive only touches
    the single credentials file the CLI authenticates with.

    Lifecycle:

      1. **First-time migration**: if the plaintext file exists
         already (legacy install) and no ciphertext yet, the
         existing plaintext is encrypted into the ciphertext and
         left in place — the CLI keeps working through the
         transition.
      2. **Steady state**: on enter, decrypt ciphertext to the
         plaintext path (mode 0o600 via ``secure_write_atomic``).
         On exit (success), read plaintext back, encrypt to
         ciphertext, remove plaintext from disk. The plaintext
         only exists between enter and exit.
      3. **On exception**: plaintext is wiped from disk
         unconditionally. Ciphertext is NOT touched, preserving
         the last-known-good state.

    Threat model
    ------------
    Same as ``EphemeralCredentialContext`` for the encryption
    properties: defends against accidental exfiltration of the
    file at rest (cloud backups, bug-report archives, syncthing).
    The exposure window during which plaintext exists at the
    canonical path is unavoidable — the CLI needs to read its
    credentials at some point — but the window is tens of
    milliseconds for typical operations and the file is mode
    0o600 throughout.

    Usage::

        ctx = InPlaceEphemeralFile(
            secure_store=secure_store,
            ciphertext_path="~/.config/unifideck/epic_tokens.bin",
            plaintext_path="~/.config/legendary/user.json",
        )
        async with ctx:
            await asyncio.create_subprocess_exec(legendary, "info", "x")
        # plaintext wiped, ciphertext refreshed if rotated
    """

    def __init__(
        self,
        *,
        secure_store: SecureTokenStore,
        ciphertext_path: str,
        plaintext_path: str,
    ) -> None:
        """Wire the dependencies for this CLI's credentials file.

        Args:
            secure_store: SecureTokenStore wrapping our key
                derivation. Same instance the parent token
                manager already owns.
            ciphertext_path: Absolute path to our UFD1 file.
            plaintext_path: Absolute path the CLI reads from
                (e.g. ``~/.config/legendary/user.json``).
        """
        self._store = secure_store
        self._ciphertext_path = ciphertext_path
        self._plaintext_path = plaintext_path
        self._wrote_plaintext = False

    async def __aenter__(self) -> None:
        """Decrypt ciphertext to plaintext path; migrate if needed.

        Three branches:

          - **Both ciphertext and plaintext absent**: nothing to
            do. The CLI will run its own auth flow which writes
            plaintext; ``__aexit__`` will encrypt it.
          - **Plaintext exists, no ciphertext**: legacy migration.
            Read plaintext, encrypt to ciphertext, leave
            plaintext alone (the CLI will keep using it during
            the call; ``__aexit__`` re-encrypts and wipes).
          - **Ciphertext exists**: normal case. Decrypt and
            write plaintext via ``secure_write_atomic``.
        """
        ciphertext_exists = await asyncio.to_thread(lambda: Path(self._ciphertext_path).is_file())
        plaintext_exists = await asyncio.to_thread(lambda: Path(self._plaintext_path).is_file())
        if not ciphertext_exists and plaintext_exists:
            # Migration: legacy install where the CLI already has
            # plaintext. Encrypt it once, then proceed normally.
            await self._migrate_legacy_plaintext()
            return
        if not ciphertext_exists:
            # Fresh install. Nothing to expose; CLI will populate
            # plaintext on its own (e.g. during OAuth).
            return
        # Normal path: decrypt → write plaintext.
        try:
            blob = await asyncio.to_thread(
                secure_read_bytes, self._ciphertext_path,
            )
        except SecureIOError as e:
            raise EphemeralCredentialError(
                f"cannot read ciphertext at "
                f"{self._ciphertext_path}: {e}",
            ) from e
        payload = self._store.decrypt_payload(blob)
        await self._write_plaintext(payload)
        self._wrote_plaintext = True
        return

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        # ``_exc`` and ``_tb`` are part of the async
        # context-manager protocol signature but are not used
        # here (only ``exc_type`` is inspected); the leading
        # underscores mark them intentionally unused so static
        # analysers (vulture) don't flag them.
        _exc: BaseException | None,
        _tb: Any,
    ) -> None:
        """Re-encrypt rotated plaintext (success); wipe in all cases.

        On clean exit: read plaintext, encrypt, save to ciphertext.
        Then wipe the plaintext file regardless. On exception:
        skip the encryption (preserves last-known-good ciphertext)
        and just wipe.
        """
        try:
            if exc_type is None:
                await self._capture_and_encrypt()
        finally:
            await self._wipe_plaintext()

    # ─── Internal helpers ──────────────────────────────────────

    async def _migrate_legacy_plaintext(self) -> None:
        """Encrypt an existing plaintext into ciphertext (one-shot).

        Called when the CLI has its plaintext but we don't have
        a ciphertext yet — typically a user upgrading from a
        Unifideck version that didn't encrypt this CLI's tokens.
        We read, encrypt, and save; the plaintext stays in place
        so the CLI keeps working through this same call.
        """
        try:
            plaintext_bytes = await asyncio.to_thread(
                secure_read_bytes, self._plaintext_path,
            )
        except SecureIOError as e:
            logger.warning(
                "[ephemeral_creds] migration: cannot read "
                "%s: %s", self._plaintext_path, e,
            )
            return
        try:
            import json
            payload = json.loads(plaintext_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning(
                "[ephemeral_creds] migration: %s is not JSON, "
                "skipping: %s", self._plaintext_path, e,
            )
            return
        if not isinstance(payload, dict):
            logger.warning(
                "[ephemeral_creds] migration: %s is not a JSON "
                "object, skipping", self._plaintext_path,
            )
            return
        blob = self._store.encrypt_payload(payload)
        try:
            await asyncio.to_thread(
                secure_write_atomic, self._ciphertext_path, blob,
            )
        except SecureIOError:
            logger.exception("[ephemeral_creds] migration: cannot write ciphertext")
            return
        logger.info(
            "[ephemeral_creds] migrated legacy plaintext at %s "
            "to encrypted store at %s",
            self._plaintext_path, self._ciphertext_path,
        )

    async def _write_plaintext(self, payload: dict[str, Any]) -> None:
        """Serialise + write the decrypted payload to plaintext path."""
        import json

        body = json.dumps(payload).encode("utf-8")
        try:
            await asyncio.to_thread(
                secure_write_atomic, self._plaintext_path, body,
            )
        except SecureIOError as e:
            raise EphemeralCredentialError(
                f"cannot write plaintext at "
                f"{self._plaintext_path}: {e}",
            ) from e

    async def _capture_and_encrypt(self) -> None:
        """Read plaintext, encrypt to ciphertext path on clean exit."""
        if not await asyncio.to_thread(lambda: Path(self._plaintext_path).is_file()):
            # CLI deleted the file (logout?). Don't touch
            # ciphertext; the parent manager will detect the
            # logged-out state on next probe.
            return
        try:
            plaintext = await asyncio.to_thread(
                secure_read_bytes, self._plaintext_path,
            )
        except SecureIOError as e:
            logger.warning(
                "[ephemeral_creds] capture: cannot read "
                "%s: %s", self._plaintext_path, e,
            )
            return
        try:
            import json
            payload = json.loads(plaintext.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning(
                "[ephemeral_creds] capture: CLI wrote unparseable "
                "JSON, keeping previous ciphertext: %s", e,
            )
            return
        if not isinstance(payload, dict):
            logger.warning(
                "[ephemeral_creds] capture: CLI wrote non-dict "
                "JSON, keeping previous ciphertext",
            )
            return
        blob = self._store.encrypt_payload(payload)
        try:
            await asyncio.to_thread(
                secure_write_atomic, self._ciphertext_path, blob,
            )
        except SecureIOError:
            logger.exception(
                "[ephemeral_creds] capture: cannot persist "
                "ciphertext — next invocation will use "
                "stale value",
            )

    async def _wipe_plaintext(self) -> None:
        """Best-effort removal of the plaintext file.

        Always called from ``__aexit__`` so plaintext does not
        outlive the context, even on exception. Failures are
        logged at debug level; we cannot raise from a finally
        path without masking the original exception (if any).
        """

        def _remove() -> None:
            try:
                # Migrated from ``os.unlink`` to ``Path.unlink`` for
                # consistency with the rest of the module (every
                # other filesystem touch in this class already goes
                # through ``Path``). ``missing_ok=True`` collapses
                # the existence check into the call itself: if the
                # file is already gone — race with another wipe,
                # external cleanup, or never written — we silently
                # succeed instead of probing then deleting.
                Path(self._plaintext_path).unlink(missing_ok=True)
            except OSError as e:
                logger.debug(
                    "[ephemeral_creds] wipe failed for %s: %s",
                    self._plaintext_path, e,
                )

        await asyncio.to_thread(_remove)
