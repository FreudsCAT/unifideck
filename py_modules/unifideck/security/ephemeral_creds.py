"""security/ephemeral_creds.py — Ephemeral plaintext exposure for CLIs.

Generic primitive that lets a third-party CLI (legendary, nile,
gogdl, …) read its credentials from disk **only for the duration
of one invocation**. Outside that window the plaintext never
exists on the filesystem; only a UFD1-encrypted copy at our
chosen path remains, protected by the same machine-id-derived
key used by every other token in Unifideck.

Threat model
------------
Closes the gap between Unifideck (which encrypts everything it
owns) and the CLIs we shell out to (which historically write
plaintext credentials in ``~/.config/<cli>/``). After integration:

  - The plaintext file exists for tens of milliseconds at most,
    inside a process-private 0o700 tempdir under ``/tmp``.
  - The persistent storage at our canonical path is UFD1-
    encrypted, machine-bound, and protected by ``secure_io``
    against symlink redirection at read time.
  - A CLI that rotates its refresh token mid-call writes the
    new value into the tempdir; the context manager reads it
    back on exit and re-encrypts before any cleanup, so token
    rotation survives across invocations.
  - On any failure (CLI crash, async cancellation, exception in
    the caller), the tempdir is wiped in ``__aexit__`` so no
    plaintext leaks past the context boundary.

Threat model NOT addressed
--------------------------

  - A local attacker watching ``/tmp`` while the CLI runs can
    snapshot the plaintext during the exposure window. We rely
    on tempdir 0o700 perms + the short window to make this
    impractical, but it is fundamentally a tradeoff: a CLI
    needs to read its credentials at some point, and on a
    Linux system that means writing them somewhere readable.
  - A CLI that resists ``XDG_CONFIG_HOME`` overrides and
    insists on ``$HOME/.config/<cli>/`` cannot be ephemeral-
    wrapped without symlinking at that path, which is a
    different (and worse) security posture. Such CLIs need a
    PR upstream.

Design
------
``EphemeralCredentialContext`` is an async context manager. The
caller hands it:

  - a ``SecureTokenStore`` (the source of truth for the
    encrypted ciphertext on disk);
  - a ``ciphertext_path`` (where the encrypted blob lives — the
    canonical path Unifideck owns);
  - a ``cli_filename`` (what the CLI expects, e.g.
    ``user.json``);
  - an ``env_var_name`` mapping the override env var the CLI
    reads (``XDG_CONFIG_HOME`` for nile, ``LEGENDARY_CONFIG_DIR``
    for legendary, ``GOGDL_CONFIG_PATH`` for gogdl).

Within the ``async with`` block the caller invokes the CLI with
the returned ``env`` dict. On exit (success OR exception) the
tempdir is unconditionally removed, and on success the (possibly
rotated) plaintext is read back and re-encrypted to the
canonical path.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .secure_io import (
    SecureIOError,
    secure_read_bytes,
    secure_write_atomic,
)
from .secure_token_store import SecureTokenStore, SecureTokenStoreError

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

# Prefix for the tempdir we create in /tmp. Helps operators
# identify what owns a leftover dir if a hard kill prevented
# cleanup. Includes the package name so it's grep-friendly.
_TEMPDIR_PREFIX = "unifideck-creds-"

# Permissions on the tempdir itself. 0o700 = owner only. Stricter
# than tempfile.mkdtemp's default (which is also 0o700 on Linux,
# but we set it explicitly so we don't rely on platform-specific
# defaults).
_TEMPDIR_MODE = 0o700


class EphemeralCredentialError(RuntimeError):
    """Raised when a critical step of the ephemeral cycle fails.

    Distinct from ``SecureTokenStoreError`` so callers can tell
    "the encrypted ciphertext is broken" (re-auth) from "the
    tempdir setup failed" (filesystem issue, retry might work).
    """


class EphemeralCredentialContext:
    """Async context manager exposing plaintext creds to a CLI.

    Usage::

        ctx = EphemeralCredentialContext(
            secure_store=secure_store,
            ciphertext_path=os.path.expanduser(
                "~/.config/unifideck/epic_tokens.bin"),
            cli_filename="user.json",
            env_var_name="LEGENDARY_CONFIG_DIR",
        )
        async with ctx as env:
            await asyncio.create_subprocess_exec(
                legendary_path, "list-games", env=env,
            )
        # tempdir is gone; persistent ciphertext refreshed if
        # the CLI rotated the token.

    Stateless across invocations — construct once per CLI call
    so the tempdir is fresh every time. Reusing one instance
    across multiple ``async with`` blocks is safe but not
    required.
    """

    def __init__(
        self,
        *,
        secure_store: SecureTokenStore,
        ciphertext_path: str,
        cli_filename: str,
        env_var_name: str,
    ) -> None:
        """Wire the dependencies + constants for this CLI.

        Args:
            secure_store: Used to encrypt/decrypt the ciphertext
                at ``ciphertext_path``. Same instance the parent
                token manager already owns.
            ciphertext_path: Absolute path to the UFD1-encrypted
                file. Created on first ``__aexit__`` if absent;
                read on subsequent ``__aenter__``.
            cli_filename: The basename the CLI expects inside
                its config dir (e.g. ``user.json`` for
                legendary, ``gog_credentials.json`` for gogdl).
            env_var_name: Name of the env var the CLI reads for
                its config-dir override. The returned ``env``
                dict has this set to the tempdir on enter.
        """
        self._store = secure_store
        self._ciphertext_path = ciphertext_path
        self._cli_filename = cli_filename
        self._env_var = env_var_name
        # Populated by ``__aenter__``, consumed by ``__aexit__``.
        self._tempdir: str | None = None
        self._plaintext_path: str | None = None

    async def __aenter__(self) -> dict[str, str]:
        """Decrypt ciphertext → write to tempdir → return env dict.

        Returns:
            A copy of ``os.environ`` with the configured override
            env var set to the tempdir. The caller passes this
            to ``subprocess`` so the CLI looks for its config
            file in the tempdir, not in ``~/.config/``.

        Raises:
            EphemeralCredentialError: tempdir creation or
                plaintext write failed.
            SecureTokenStoreError: ciphertext is corrupt or
                machine-id can't decrypt it.
        """
        payload = await self._load_payload()
        tempdir = await self._make_tempdir()
        self._tempdir = tempdir
        self._plaintext_path = os.path.join(tempdir, self._cli_filename)
        await self._write_plaintext(payload)
        env = os.environ.copy()
        env[self._env_var] = tempdir
        logger.debug(
            "[ephemeral_creds] exposed %s in %s for CLI",
            self._cli_filename, tempdir,
        )
        return env

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        """Re-encrypt rotated plaintext (on success) + wipe tempdir.

        Two-step exit:

          1. If the caller returned normally and the plaintext
             file still exists, read it back. If its contents
             differ from what we wrote in ``__aenter__``, the
             CLI rotated the token; re-encrypt to the canonical
             ciphertext path so the next invocation sees the
             fresh value.
          2. Unconditionally wipe the tempdir, even on
             exception, so plaintext never outlives the
             context.

        Cleanup is best-effort: failures are logged but never
        raised, otherwise the original exception (if any)
        would be masked.
        """
        try:
            if exc_type is None:
                await self._capture_rotation_if_any()
        finally:
            await self._wipe_tempdir()

    # ─── Internal helpers ──────────────────────────────────────

    async def _load_payload(self) -> dict[str, Any]:
        """Decrypt the ciphertext at the canonical path.

        Returns the payload dict, or an empty dict if the file
        does not exist yet (first invocation). Any other error
        (corrupt, wrong machine-id, I/O refusal) is escalated
        as ``EphemeralCredentialError`` so the caller can
        surface "re-auth needed" to the user.
        """
        if not Path(self._ciphertext_path).is_file():
            # First-time use: nothing to expose, the CLI will
            # do its own auth flow inside the tempdir and we
            # capture the result on exit.
            return {}
        try:
            blob = await asyncio.to_thread(
                secure_read_bytes, self._ciphertext_path,
            )
        except SecureIOError as e:
            raise EphemeralCredentialError(
                f"cannot read ciphertext at "
                f"{self._ciphertext_path}: {e}",
            ) from e
        try:
            return self._store.decrypt_payload(blob)
        except SecureTokenStoreError as e:
            # Re-raise unchanged — callers expect this exact
            # type when the ciphertext is unreadable, so they
            # can trigger a re-auth flow.
            raise SecureTokenStoreError(
                f"ciphertext at {self._ciphertext_path} is "
                f"unusable: {e}",
            ) from e

    @staticmethod
    async def _make_tempdir() -> str:
        """Create a fresh 0o700 tempdir for the exposure window.

        Wraps ``tempfile.mkdtemp`` in ``asyncio.to_thread`` so
        we don't block the event loop on the syscall (mkdtemp
        is fast but always blocks for the duration). Sets the
        mode explicitly even though Linux defaults match —
        defense in depth.
        """
        try:
            tempdir = await asyncio.to_thread(
                tempfile.mkdtemp, prefix=_TEMPDIR_PREFIX,
            )
        except OSError as e:
            raise EphemeralCredentialError(
                f"cannot create tempdir for ephemeral creds: {e}",
            ) from e
        # mkdtemp creates 0o700 on Linux already, but we set it
        # explicitly so the invariant doesn't depend on
        # platform.
        try:
            os.chmod(tempdir, _TEMPDIR_MODE)
        except OSError as e:
            # If we can't chmod, abort and remove — we cannot
            # let plaintext live in a dir we don't fully
            # control.
            with contextlib.suppress(OSError):
                os.rmdir(tempdir)
            raise EphemeralCredentialError(
                f"cannot chmod tempdir {tempdir}: {e}",
            ) from e
        return tempdir

    async def _write_plaintext(self, payload: dict[str, Any]) -> None:
        """Serialise ``payload`` to JSON and write to the tempdir.

        Uses the standard library ``json`` so the on-disk
        format matches whatever the CLI expects. The file is
        created at mode 0o600 via ``secure_write_atomic`` —
        this is the same hardened path that protects the
        canonical UFD1 file, applied here for the brief
        plaintext window.
        """
        assert self._plaintext_path is not None
        import json  # noqa: PLC0415 — stdlib, lazy

        # Empty payload still writes a valid JSON object so the
        # CLI does not see EOF and can populate the file as
        # part of its own auth flow.
        body = json.dumps(payload).encode("utf-8")
        try:
            await asyncio.to_thread(
                secure_write_atomic, self._plaintext_path, body,
            )
        except SecureIOError as e:
            raise EphemeralCredentialError(
                f"cannot write ephemeral plaintext at "
                f"{self._plaintext_path}: {e}",
            ) from e

    async def _capture_rotation_if_any(self) -> None:
        """Re-encrypt the plaintext if the CLI rotated the token.

        Reads the plaintext file (if it still exists), parses
        as JSON, encrypts to the canonical ciphertext path. If
        the CLI deleted the file (e.g. logout-from-CLI),
        nothing to capture; we leave the canonical ciphertext
        as-is for now and let the parent token manager decide
        on next invocation.
        """
        assert self._plaintext_path is not None
        try:
            plaintext = await asyncio.to_thread(
                secure_read_bytes, self._plaintext_path,
            )
        except SecureIOError:
            # Plaintext gone — CLI must have logged out or
            # crashed mid-write. Don't touch the ciphertext.
            logger.debug(
                "[ephemeral_creds] no plaintext to capture at exit",
            )
            return
        try:
            import json  # noqa: PLC0415 — stdlib, lazy
            payload = json.loads(plaintext.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning(
                "[ephemeral_creds] CLI wrote unparseable JSON, "
                "keeping previous ciphertext: %s", e,
            )
            return
        if not isinstance(payload, dict):
            logger.warning(
                "[ephemeral_creds] CLI wrote non-dict JSON "
                "(%s), keeping previous ciphertext",
                type(payload).__name__,
            )
            return
        blob = self._store.encrypt_payload(payload)
        try:
            await asyncio.to_thread(
                secure_write_atomic, self._ciphertext_path, blob,
            )
        except SecureIOError as e:
            logger.error(
                "[ephemeral_creds] cannot persist rotated "
                "ciphertext: %s — next invocation will use "
                "the stale value", e,
            )

    async def _wipe_tempdir(self) -> None:
        """Remove the tempdir and every file in it.

        Called from ``__aexit__`` unconditionally so plaintext
        never outlives the context, even on exception. Ignores
        all errors — operators can clean up leftover
        ``unifideck-creds-*`` dirs in /tmp if they ever
        accumulate (they shouldn't, but a hard kill -9 of
        Decky would leak them and we don't want a future
        invocation to crash on prior leftovers).
        """
        if self._tempdir is None:
            return
        tempdir = self._tempdir
        self._tempdir = None
        self._plaintext_path = None

        def _rmtree() -> None:
            with contextlib.suppress(FileNotFoundError):
                # Walk + remove. We don't use shutil.rmtree
                # because it follows symlinks by default in
                # older Pythons, and we want to be precise
                # about what we delete.
                for entry in _safe_listdir(tempdir):
                    full = os.path.join(tempdir, entry)
                    with contextlib.suppress(OSError):
                        os.unlink(full)
                with contextlib.suppress(OSError):
                    os.rmdir(tempdir)

        await asyncio.to_thread(_rmtree)


def _safe_listdir(path: str) -> Iterator[str]:
    """Yield directory entries, swallowing OSError."""
    try:
        yield from os.listdir(path)
    except OSError as e:
        logger.debug(
            "[ephemeral_creds] listdir failed during cleanup: %s", e,
        )


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
        ciphertext_exists = Path(self._ciphertext_path).is_file()
        plaintext_exists = Path(self._plaintext_path).is_file()
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
        exc: BaseException | None,
        tb: Any,
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
            import json  # noqa: PLC0415 — stdlib, lazy
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
        except SecureIOError as e:
            logger.error(
                "[ephemeral_creds] migration: cannot write "
                "ciphertext: %s", e,
            )
            return
        logger.info(
            "[ephemeral_creds] migrated legacy plaintext at %s "
            "to encrypted store at %s",
            self._plaintext_path, self._ciphertext_path,
        )

    async def _write_plaintext(self, payload: dict[str, Any]) -> None:
        """Serialise + write the decrypted payload to plaintext path."""
        import json  # noqa: PLC0415 — stdlib, lazy

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
        if not Path(self._plaintext_path).is_file():
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
            import json  # noqa: PLC0415 — stdlib, lazy
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
        except SecureIOError as e:
            logger.error(
                "[ephemeral_creds] capture: cannot persist "
                "ciphertext: %s — next invocation will use "
                "stale value", e,
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
                if Path(self._plaintext_path).is_file():
                    os.unlink(self._plaintext_path)
            except OSError as e:
                logger.debug(
                    "[ephemeral_creds] wipe failed for %s: %s",
                    self._plaintext_path, e,
                )

        await asyncio.to_thread(_remove)
