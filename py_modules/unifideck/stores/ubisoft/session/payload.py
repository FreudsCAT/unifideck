"""payload.py — Sync UPC credentials/auth artifacts between prefixes.

# OP-60b | py_modules/unifideck/stores/ubisoft/session/payload.py | Depends: (none)
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
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
    ) -> int:
        """Sync payload to prefix."""
        if self.should_skip_payload_sync(
            source_prefix, target_prefix, payload_sources, apply_dpapi_guard,
        ):
            return 0
        copied = 0
        for rel_path, src_path in payload_sources.items():
            dst_path = os.path.join(target_prefix, rel_path)
            if self.copy_payload_entry(
                src_path, dst_path,
                handle_directories=handle_directories,
                log_label=log_label, rel_path=rel_path,
            ):
                copied += 1
        if copied:
            logger.info(
                '[Ubisoft.session] %s synced %d items %s → %s',
                log_label, copied, source_prefix, target_prefix,
            )
        return copied

    def should_skip_payload_sync(
        self,
        source_prefix: str, target_prefix: str,
        payload_sources: dict[str, str], apply_dpapi_guard: bool,
    ) -> bool:
        """Check whether skip payload sync."""
        if source_prefix == target_prefix:
            return True
        if not payload_sources:
            return True
        if apply_dpapi_guard:
            try:
                src_guid = self._parent._read_machine_guid(source_prefix)
                dst_guid = self._parent._read_machine_guid(target_prefix)
            except Exception:
                src_guid = dst_guid = ''
            if src_guid and dst_guid and src_guid != dst_guid:
                logger.debug(
                    '[Ubisoft.session] DPAPI guard: machineGuid mismatch',
                )
                return True
        return False

    def copy_payload_entry(
        self,
        src_path: str, dst_path: str, *,
        handle_directories: bool, log_label: str, rel_path: str,
    ) -> bool:
        """Copy payload entry."""
        if not os.path.exists(src_path):
            return False
        try:
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            if os.path.isdir(src_path):
                if not handle_directories:
                    return False
                if os.path.isdir(dst_path):
                    shutil.rmtree(dst_path, ignore_errors=True)
                shutil.copytree(src_path, dst_path)
            else:
                shutil.copy2(src_path, dst_path)
            return True
        except OSError as e:
            logger.warning(
                '[Ubisoft.session] %s copy %s failed: %s',
                log_label, rel_path, e,
            )
            return False

    def sync_credentials_to_prefix(
        self, source_prefix: str, target_prefix: str,
    ) -> int:
        """Sync credentials to prefix."""
        sources = self.collect_credential_sources(source_prefix)
        return self.sync_payload_to_prefix(
            source_prefix, target_prefix,
            payload_sources=sources,
            apply_dpapi_guard=True,
            handle_directories=False,
            log_label='credentials',
        )

    def collect_credential_sources(self, source_prefix: str) -> dict[str, str]:
        """Collect credential sources."""
        config = self._parent._config
        paths_helper = self._parent._paths
        sources: dict[str, str] = {}
        for prefix_root, user_home in paths_helper.iter_user_homes(
            source_prefix, pfx_first=True,
        ):
            for filename in config.upc_credential_files:
                full = os.path.join(
                    user_home, config.upc_local_subdir, filename,
                )
                if os.path.isfile(full) and os.path.getsize(full) >= _CSS_MIN_SOURCE_SIZE:
                    rel = os.path.relpath(full, prefix_root)
                    sources.setdefault(rel, full)
        return sources

    def sync_auth_artifacts_to_prefix(
        self, source_prefix: str, target_prefix: str,
    ) -> int:
        """Sync auth artifacts to prefix."""
        sources = self.collect_artifact_sources(source_prefix)
        return self.sync_payload_to_prefix(
            source_prefix, target_prefix,
            payload_sources=sources,
            apply_dpapi_guard=False,
            handle_directories=True,
            log_label='auth_artifacts',
        )

    def collect_artifact_sources(self, source_prefix: str) -> dict[str, str]:
        """Collect artifact sources."""
        config = self._parent._config
        paths_helper = self._parent._paths
        sources: dict[str, str] = {}
        for prefix_root, user_home in paths_helper.iter_user_homes(
            source_prefix, pfx_first=True,
        ):
            for artifact in config.upc_auth_cache_artifacts:
                full = os.path.join(
                    user_home, config.upc_local_subdir, artifact,
                )
                if os.path.exists(full):
                    rel = os.path.relpath(full, prefix_root)
                    sources.setdefault(rel, full)
        return sources

    @staticmethod
    def hash_artifact(path: str) -> str:
        """Hash artifact."""
        digest = hashlib.sha256()
        if not os.path.exists(path):
            return ''
        try:
            if os.path.isdir(path):
                _PayloadSync._hash_directory_into(digest, path)
            else:
                _PayloadSync._hash_file_into(digest, path)
        except OSError:
            return ''
        return digest.hexdigest()

    @staticmethod
    def _hash_directory_into(digest: hashlib._Hash, path: str) -> None:
        """Hash directory into."""
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for fname in sorted(files):
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, path).encode('utf-8')
                digest.update(rel)
                _PayloadSync._hash_file_into(digest, full)

    @staticmethod
    def _hash_file_into(digest: hashlib._Hash, path: str) -> None:
        """Hash file into."""
        try:
            with open(path, 'rb') as f:
                while True:
                    chunk = f.read(_HASH_CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError:
            pass
