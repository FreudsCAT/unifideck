"""Encrypted token persistence — load, persist, clear.

OP-52b | py_modules/unifideck/stores/gog/tokens/storage.py

``_TokenStorage`` is responsible for the on-disk representation of GOG
tokens. The file lives at ``GOGConfig.token_file_expanded`` and is
encrypted via ``SecureTokenStore`` — refusing to fall back to plaintext
if encryption is unavailable (we'd rather lose the session than leak
the refresh token).

Atomic write: tokens are written through ``os.open`` + ``os.fdopen``
+ ``os.replace`` with ``mode=0o600`` set at creation time. This is
deliberately kept verbose because ``Path.open`` doesn't support the
UNIX permission mode argument and ``Path.rename`` isn't atomic across
all filesystems.

Migrates legacy plaintext token files on the fly: reads them
unencrypted (emits ``legacy_plaintext_detected``), and re-encrypts at
the next ``persist`` call.

Also cleans up the stale gogdl plaintext mirror that gogdl writes to
its config dir after every subprocess invocation (security hardening).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from unifideck.security import (
    SecureTokenStore,
    SecureTokenStoreError,
    emit_legacy_plaintext_detected,
    emit_permissions_check,
    emit_token_file_migrated,
)

from .user_info import GOGUserInfo

if TYPE_CHECKING:
    from unifideck.stores.gog.config import GOGConfig
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

    async def load(self) -> tuple[str, str, GOGUserInfo] | None:
        """Load."""
        path = await asyncio.to_thread(lambda: str(Path(self._config.token_file).expanduser()))
        if not await asyncio.to_thread(lambda: Path(path).is_file()):
            return None

        def _read_sync() -> bytes | None:
            """Read sync."""
            try:
                with Path(path).open("rb") as f:
                    return f.read()
            except OSError as e:
                logger.warning("[GOGTokens] load failed: %s", e)
                return None

        blob = await asyncio.to_thread(_read_sync)
        if blob is None:
            return None
        data = self._parse_token_blob(blob, path)
        if not isinstance(data, dict):
            return None
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if not access or not refresh:
            return None
        user_info = GOGUserInfo(
            username=str(data.get("username", "")),
            galaxy_user_id=str(data.get("user_id", "")),
        )
        logger.info(
            "[GOGTokens] loaded tokens from disk (user=%s)",
            user_info.username or "unknown",
        )
        return access, refresh, user_info

    async def persist(
        self,
        access_token: str,
        refresh_token: str,
        user_info: GOGUserInfo,
    ) -> bool:
        """Persist."""
        path = await asyncio.to_thread(lambda: str(Path(self._config.token_file).expanduser()))
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "username": user_info.username,
            "user_id": user_info.galaxy_user_id,
        }
        try:
            blob = self._secure_store.encrypt_payload(payload)
        except SecureTokenStoreError:
            logger.exception(
                "[GOGTokens] cannot encrypt tokens — "
                "refusing to write plaintext fallback",
            )
            return False
        ok = await asyncio.to_thread(
            self._write_token_file_atomic,
            path,
            blob,
        )
        if not ok:
            return False
        await self._remove_stale_gogdl_mirror()
        await self._emit_post_save_security(path)
        logger.info("[GOGTokens] saved tokens (encrypted)")
        return True

    async def clear_files(self) -> None:
        """Clear files."""
        def _resolve_paths() -> list[str]:
            """Resolve all credential paths synchronously (off the loop)."""
            return [
                str(Path(self._config.token_file).expanduser()),
                str(Path(str(Path(self._config.gogdl_config_dir).expanduser())) / "gog_credentials.json"),
            ]

        paths_to_remove = await asyncio.to_thread(_resolve_paths)

        def _remove_sync() -> None:
            """Remove sync."""
            for path in paths_to_remove:
                if not Path(path).is_file():
                    continue
                try:
                    Path(path).unlink()
                    logger.info(
                        "[GOGTokens] removed %s",
                        path,
                    )
                except OSError as e:
                    logger.warning(
                        "[GOGTokens] could not remove %s: %s",
                        path,
                        e,
                    )

        await asyncio.to_thread(_remove_sync)

    @staticmethod
    def _write_token_file_atomic(path: str, blob: bytes) -> bool:
        """Write token file atomic."""
        try:
            parent = str(Path(path).parent)
            if parent:
                Path(parent).mkdir(parents=True, exist_ok=True)
            tmp = path + ".tmp"
            fd = os.open(
                tmp,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
            Path(tmp).replace(path)
        except OSError as e:
            logger.warning(
                "[GOGTokens] save failed: %s",
                e,
            )
            return False
        return True

    async def _emit_post_save_security(self, path: str) -> None:
        """Emit post save security."""

        def _stat_mode() -> int | None:
            """Stat mode."""
            try:
                st = Path(path).stat()
                return st.st_mode & 0o7777
            except OSError:
                return None

        mode = await asyncio.to_thread(_stat_mode)
        if mode is not None:
            emit_permissions_check(
                self._bus,
                "gog",
                path,
                mode,
            )

    def _parse_token_blob(self, blob: bytes, path: str) -> dict[str, Any] | None:
        """Parse token blob."""
        if self._secure_store.is_encrypted(blob):
            try:
                return self._secure_store.decrypt_payload(blob)
            except SecureTokenStoreError as e:
                logger.warning(
                    "[GOGTokens] decrypt failed for %s: %s",
                    path,
                    e,
                )
                return None
        logger.info(
            "[GOGTokens] reading legacy plaintext token file "
            "at %s — will encrypt on next save",
            path,
        )
        emit_legacy_plaintext_detected(self._bus, "gog", path)
        try:
            return cast(
                "dict[str, Any] | None",
                json.loads(blob.decode("utf-8")),
            )
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning(
                "[GOGTokens] legacy JSON parse failed: %s",
                e,
            )
            return None

    async def _remove_stale_gogdl_mirror(self) -> None:
        """Remove stale GOGDL mirror."""
        stale = str(Path(await asyncio.to_thread(lambda: str(Path(self._config.gogdl_config_dir).expanduser()))) / "gog_credentials.json")

        def _remove() -> bool:
            """Remove."""
            if not Path(stale).is_file():
                return False
            try:
                Path(stale).unlink()
                logger.info(
                    "[GOGTokens] removed stale gogdl mirror at %s",
                    stale,
                )
                return True
            except OSError as e:
                logger.warning(
                    "[GOGTokens] could not remove stale gogdl mirror %s: %s",
                    stale,
                    e,
                )
                return False

        removed = await asyncio.to_thread(_remove)
        if removed:
            emit_token_file_migrated(
                self._bus,
                "gog",
                stale,
                "",
            )


_ = time
