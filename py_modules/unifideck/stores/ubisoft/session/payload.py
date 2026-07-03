"""
UPC payload sync between Wine prefixes.

OP-60b | py_modules/unifideck/stores/ubisoft/session/payload.py

``_PayloadSync`` copies credentials and auth-cache artifacts from one
Wine prefix to another. Two kinds of payload exist:

* **credentials** (``ConnectSecureStorage.dat``, ``user.dat``) —
  DPAPI-encrypted, bound to the machine GUID; sync requires the
  GUID match.
* **auth-cache artifacts** (settings, cookies, http2 cache, ownership
  cache) — not DPAPI-protected, sync without the guard.

The sync is idempotent: artifacts are hashed before copying so identical
files aren't re-copied. The hash function preserves a strict ordering
(files sorted alphabetically per directory, sub-dirs in filesystem
order) to keep digest stability across runs — caches built before this
ordering policy was applied may produce different hashes and trigger a
one-time re-sync.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import UbisoftSession
logger = logging.getLogger(__name__)
_CSS_MIN_SOURCE_SIZE = 10
_HASH_CHUNK_SIZE = 1024 * 1024


class _PayloadSync:
    """Payload sync."""

    def __init__(self, parent: UbisoftSession) -> None:
        """Initialize the instance."""
        self._parent = parent

    def sync_payload_to_prefix(
        self,
        source_prefix: str,
        target_prefix: str,
        *,
        payload_sources: dict[str, str],
        apply_dpapi_guard: bool,
        handle_directories: bool,
        log_label: str,
        skip_if_smaller: bool = False,
    ) -> int:
        """Sync payload to prefix."""
        if self.should_skip_payload_sync(
            source_prefix,
            target_prefix,
            payload_sources,
            apply_dpapi_guard,
        ):
            return 0
        synced = 0
        for _root, user_home in self._parent._paths.iter_user_homes(target_prefix):
            target_root = str(Path(user_home) / self._parent._config.upc_local_subdir)
            for rel_path, src_path in payload_sources.items():
                dst_path = str(Path(target_root) / rel_path)
                if skip_if_smaller and self._is_credential_regression(
                    src_path, dst_path, log_label, rel_path,
                ):
                    continue
                if self.copy_payload_entry(
                    src_path,
                    dst_path,
                    handle_directories=handle_directories,
                    log_label=log_label,
                    rel_path=rel_path,
                ):
                    synced += 1
        return synced

    def should_skip_payload_sync(
        self,
        source_prefix: str,
        target_prefix: str,
        payload_sources: dict[str, str],
        apply_dpapi_guard: bool,
    ) -> bool:
        """Check whether skip payload sync."""
        if os.path.realpath(source_prefix) == os.path.realpath(target_prefix):
            return True
        if not payload_sources:
            return True
        if apply_dpapi_guard:
            source_guid = self._parent._read_machine_guid(
                source_prefix,
            )
            target_guid = self._parent._read_machine_guid(
                target_prefix,
            )
            if source_guid and target_guid and source_guid != target_guid:
                logger.warning(
                    "[UbisoftSession] MachineGuid mismatch: "
                    "source=%s… target=%s… — skipping "
                    "DPAPI sync",
                    source_guid[:8],
                    target_guid[:8],
                )
                return True
        return False

    @staticmethod
    def _is_credential_regression(
        src_path: str,
        dst_path: str,
        log_label: str,
        rel_path: str,
    ) -> bool:
        """True if overwriting *dst* with *src* would shrink a credential.

        UPC's ``ConnectSecureStorage.dat`` shrinks when a session logs out
        (the token is stripped). Capture/propagation is otherwise
        login/logout blind and picks the freshest file, so a single logout
        in ONE prefix would overwrite the logged-in credential everywhere
        (observed: an SD-card install logging out poisoned auth + every game
        prefix). A real re-login is same-size-or-larger and still flows;
        explicit sign-out deletes (not copies) so it is unaffected.
        """
        if not (Path(dst_path).is_file() and Path(src_path).is_file()):
            return False
        try:
            src_sz = Path(src_path).stat().st_size
            dst_sz = Path(dst_path).stat().st_size
        except OSError:
            return False
        if src_sz and dst_sz and src_sz < dst_sz:
            logger.info(
                "[UbisoftSession] %s: keeping logged-in %s — refusing to "
                "overwrite with a smaller (logged-out?) copy (%d < %d bytes)",
                log_label,
                rel_path,
                src_sz,
                dst_sz,
            )
            return True
        return False

    def copy_payload_entry(
        self,
        src_path: str,
        dst_path: str,
        *,
        handle_directories: bool,
        log_label: str,
        rel_path: str,
    ) -> bool:
        """Copy payload entry."""
        if Path(dst_path).exists():
            try:
                same = self.hash_artifact(src_path) == self.hash_artifact(dst_path)
            except OSError:
                same = False
            if same:
                return False
        try:
            parent = str(Path(dst_path).parent)
            if parent:
                Path(parent).mkdir(parents=True, exist_ok=True)
            if handle_directories:
                if Path(dst_path).is_dir():
                    shutil.rmtree(
                        dst_path,
                        ignore_errors=True,
                    )
                elif Path(dst_path).exists():
                    Path(dst_path).unlink()
                if Path(src_path).is_dir():
                    shutil.copytree(src_path, dst_path)
                else:
                    shutil.copy2(src_path, dst_path)
            else:
                shutil.copy2(src_path, dst_path)
            return True
        except OSError as e:
            logger.warning(
                "[UbisoftSession] %s copy failed for %s: %s",
                log_label,
                rel_path,
                e,
            )
            return False

    def purge_credentials_from_prefix(self, target_prefix: str) -> int:
        """Delete UPC credentials + auth-cache artifacts from a prefix.

        The inverse of :meth:`sync_credentials_to_prefix` /
        :meth:`sync_auth_artifacts_to_prefix`: removes the same entries
        (``upc_credential_files`` + ``upc_auth_cache_artifacts``) so a
        signed-out prefix can no longer be picked up as a credential
        fallback source by
        :meth:`_CredentialReader.find_best_credential_source` (which would
        otherwise silently re-authenticate the user on the next launch).
        Returns the number of entries removed.
        """
        config = self._parent._config
        rel_entries = (
            *config.upc_credential_files,
            *config.upc_auth_cache_artifacts,
        )
        removed = 0
        for _root, user_home in self._parent._paths.iter_user_homes(
            target_prefix,
        ):
            local_root = Path(user_home) / config.upc_local_subdir
            for rel in rel_entries:
                if self._remove_credential_path(local_root / rel, rel):
                    removed += 1
        return removed

    @staticmethod
    def _remove_credential_path(target: Path, rel: str) -> bool:
        """Delete ``target`` (directory or file); True if it removed
        something, False if it was absent or removal failed."""
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                return True
            if target.exists():
                target.unlink()
                return True
        except OSError as e:
            logger.warning(
                "[UbisoftSession] purge failed for %s: %s", rel, e,
            )
        return False

    def sync_credentials_to_prefix(
        self,
        source_prefix: str,
        target_prefix: str,
    ) -> int:
        """Sync credentials to prefix."""
        return self.sync_payload_to_prefix(
            source_prefix=source_prefix,
            target_prefix=target_prefix,
            payload_sources=self.collect_credential_sources(
                source_prefix,
            ),
            apply_dpapi_guard=True,
            handle_directories=False,
            log_label="credential",
            skip_if_smaller=True,
        )

    def collect_credential_sources(
        self,
        source_prefix: str,
    ) -> dict[str, str]:
        """Collect credential sources."""
        source_files: dict[str, str] = {}
        for _root, user_home in self._parent._paths.iter_user_homes(
            source_prefix,
            pfx_first=True,
        ):
            for fname in self._parent._config.upc_credential_files:
                if fname in source_files:
                    continue
                src = str(Path(user_home) / self._parent._config.upc_local_subdir / fname)
                if self._parent._is_valid_css(
                    src,
                    _CSS_MIN_SOURCE_SIZE,
                ):
                    source_files[fname] = src
        return source_files

    def sync_auth_artifacts_to_prefix(
        self,
        source_prefix: str,
        target_prefix: str,
    ) -> int:
        """Sync auth artifacts to prefix."""
        return self.sync_payload_to_prefix(
            source_prefix=source_prefix,
            target_prefix=target_prefix,
            payload_sources=self.collect_artifact_sources(
                source_prefix,
            ),
            apply_dpapi_guard=False,
            handle_directories=True,
            log_label="auth cache artifact",
        )

    def collect_artifact_sources(
        self,
        source_prefix: str,
    ) -> dict[str, str]:
        """Collect artifact sources."""
        artifacts: dict[str, str] = {}
        for _root, user_home in self._parent._paths.iter_user_homes(
            source_prefix,
            pfx_first=True,
        ):
            local_root = str(Path(user_home) / self._parent._config.upc_local_subdir)
            for rel_path in self._parent._config.upc_auth_cache_artifacts:
                if rel_path in artifacts:
                    continue
                candidate = str(Path(local_root) / rel_path)
                if Path(candidate).is_file() or Path(candidate).is_dir():
                    artifacts[rel_path] = candidate
        return artifacts

    @staticmethod
    def hash_artifact(path: str) -> str:
        """Check whether artifact."""
        digest = hashlib.sha256()
        if Path(path).is_dir():
            _PayloadSync._hash_directory_into(digest, path)
        elif Path(path).is_file():
            _PayloadSync._hash_file_into(digest, path)
        return digest.hexdigest()

    @staticmethod
    def _hash_directory_into(digest: hashlib._Hash, path: str) -> None:
        """Hash directory into."""
        for root, _dirs, files in os.walk(path):
            files.sort()
            for name in files:
                file_path = str(Path(root) / name)
                rel_path = os.path.relpath(file_path, path)
                digest.update(rel_path.encode("utf-8"))
                _PayloadSync._hash_file_into(digest, file_path)

    @staticmethod
    def _hash_file_into(digest: hashlib._Hash, path: str) -> None:
        """Hash file into."""
        with (
            contextlib.suppress(OSError),
            Path(path).open("rb") as f,
        ):
            for chunk in iter(
                lambda: f.read(_HASH_CHUNK_SIZE),
                b"",
            ):
                digest.update(chunk)
