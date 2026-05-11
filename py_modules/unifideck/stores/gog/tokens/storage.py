"""storage.py — On-disk store for GOG tokens.

# OP-52b | py_modules/unifideck/stores/gog/tokens/storage.py | Depends: (none)

Token file is encrypted via :class:`SecureTokenStore` when possible
and degrades to plaintext when the secure store can't be opened.
Plaintext reads emit a ``LEGACY_PLAINTEXT_DETECTED`` audit event so
the deployment can be flagged for migration.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ....security import (
    SecureTokenStore,
    SecureTokenStoreError,
    emit_legacy_plaintext_detected,
    emit_permissions_check,
    emit_token_file_migrated,
)
from .user_info import GOGUserInfo

if TYPE_CHECKING:
    from ..config import GOGConfig

logger = logging.getLogger(__name__)


class _TokenStorage:
    """Token storage."""

    def __init__(
        self,
        *,
        config: GOGConfig,
        bus: Any,
        secure_store: SecureTokenStore,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._bus = bus
        self._secure_store = secure_store
        self._token_path = os.path.expanduser(config.token_file)

    async def load(self) -> tuple[str, str, GOGUserInfo] | None:
        """Load."""
        path = self._token_path
        if not Path(path).is_file():
            return None
        blob = await asyncio.to_thread(self._read_sync)
        if blob is None:
            return None
        data = self._parse_token_blob(blob, path)
        if data is None:
            return None
        access = data.get('access_token')
        refresh = data.get('refresh_token')
        if not isinstance(access, str) or not isinstance(refresh, str):
            return None
        user = data.get('user_info') or {}
        info = GOGUserInfo(
            username=str(user.get('username', '')),
            galaxy_user_id=str(user.get('galaxy_user_id', '')),
        )
        return access, refresh, info

    async def persist(
        self,
        access_token: str,
        refresh_token: str,
        user_info: GOGUserInfo,
    ) -> bool:
        """Persist."""
        payload: dict[str, Any] = {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user_info': {
                'username': user_info.username,
                'galaxy_user_id': user_info.galaxy_user_id,
            },
            'saved_at': int(time.time()),
        }
        try:
            blob = self._secure_store.encrypt_payload(payload)
        except SecureTokenStoreError as e:
            logger.warning('[GOGTokens] encrypt failed, plaintext: %s', e)
            blob = json.dumps(payload).encode('utf-8')
        ok = await asyncio.to_thread(
            self._write_token_file_atomic, self._token_path, blob,
        )
        if ok:
            await self._emit_post_save_security(self._token_path)
            await asyncio.to_thread(self._remove_stale_gogdl_mirror)
        return ok

    async def clear_files(self) -> None:
        """Clear files."""
        for path in (
            self._token_path,
            os.path.expanduser(self._gogdl_creds_path()),
        ):
            try:
                if os.path.isfile(path):
                    os.unlink(path)
            except OSError as e:
                logger.debug('[GOGTokens] unlink %s: %s', path, e)

    @staticmethod
    def _write_token_file_atomic(path: str, blob: bytes) -> bool:
        """Write token file atomic."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(blob)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as e:
            logger.warning('[GOGTokens] write %s: %s', path, e)
            return False
        return True

    async def _emit_post_save_security(self, path: str) -> None:
        """Emit post save security."""
        try:
            mode = os.stat(path).st_mode & 0o777 if os.path.isfile(path) else None
        except OSError:
            mode = None
        if mode is not None:
            await emit_permissions_check(
                self._bus, store='gog', path=path, mode=mode,
            )

    def _parse_token_blob(
        self, blob: bytes, path: str,
    ) -> dict[str, Any] | None:
        """Parse token blob."""
        if self._secure_store.is_encrypted(blob):
            try:
                return self._secure_store.decrypt_payload(blob)
            except SecureTokenStoreError as e:
                logger.warning('[GOGTokens] decrypt %s: %s', path, e)
                return None
        try:
            data = json.loads(blob.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning('[GOGTokens] parse %s: %s', path, e)
            return None
        if isinstance(data, dict):
            asyncio.get_event_loop_policy()
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    emit_legacy_plaintext_detected(
                        self._bus, store='gog', path=path,
                    ),
                    name='gog_legacy_plaintext_detected',
                )
            except RuntimeError:
                pass
            return cast(dict[str, Any], data)
        return None

    async def _emit_post_save_security(self, path: str) -> None:  # noqa: F811
        """Emit post save security (kept for symmetry with PDF spec)."""
        try:
            mode = os.stat(path).st_mode & 0o777 if os.path.isfile(path) else None
        except OSError:
            mode = None
        if mode is not None:
            await emit_permissions_check(
                self._bus, store='gog', path=path, mode=mode,
            )

    def _read_sync(self) -> bytes | None:
        """Read sync."""
        try:
            with open(self._token_path, 'rb') as f:
                return f.read()
        except OSError as e:
            logger.warning('[GOGTokens] read failed: %s', e)
            return None

    def _gogdl_creds_path(self) -> str:
        """GOGDL creds path."""
        return os.path.join(self._config.gogdl_config_dir, 'credentials.json')

    def _remove_stale_gogdl_mirror(self) -> None:
        """Remove stale GOGDL mirror.

        After a fresh secure-store write we drop the old plaintext
        gogdl credentials mirror so the next ``gogdl`` invocation
        re-mints it from the live tokens.
        """
        mirror = os.path.expanduser(self._gogdl_creds_path())
        try:
            if os.path.isfile(mirror):
                os.unlink(mirror)
                _emit_migrated(self._bus)
        except OSError as e:
            logger.debug('[GOGTokens] mirror unlink: %s', e)


def _emit_migrated(bus: Any) -> None:
    """Emit migrated.

    Schedules ``emit_token_file_migrated`` on the running loop if any.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        emit_token_file_migrated(bus, store='gog'),
        name='gog_token_file_migrated',
    )


_ = time
