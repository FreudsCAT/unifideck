from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from unifideck.security import (
    SecureTokenStore,
    SecureTokenStoreError,
    emit_legacy_plaintext_detected,
    emit_permissions_check,
)

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.stores.microsoft.microsoft_config import MicrosoftConfig
logger = logging.getLogger(__name__)
class PersistenceMixin:
    """Persistence mixin."""
    _ms_access_token: str | None
    _ms_refresh_token: str | None
    _token_saved_at: float
    _config: MicrosoftConfig
    _secure_store: SecureTokenStore
    _bus: EventBus | None
    async def load(self) -> bool:
        """Load."""
        path = str(await asyncio.to_thread(lambda: Path(self._config.token_file).expanduser()))
        if not await asyncio.to_thread(lambda: Path(path).is_file()):
            return False
        def _read_sync() -> bytes | None:
            """Read sync."""
            try:
                return Path(path).read_bytes()
            except OSError as e:
                logger.warning(
                    "[MicrosoftTokens] load failed: %s", e,
                )
                return None
        blob = await asyncio.to_thread(_read_sync)
        if blob is None:
            return False
        data = self._parse_blob(blob, path)
        if not isinstance(data, dict):
            return False
        refresh = data.get("refresh_token")
        if not refresh:
            self._ms_access_token = None
            self._ms_refresh_token = None
            self._token_saved_at = 0.0
            return False
        self._ms_access_token = data.get("access_token") or None
        self._ms_refresh_token = refresh
        try:
            self._token_saved_at = float(
                data.get("saved_at", 0.0),
            )
        except (TypeError, ValueError):
            self._token_saved_at = 0.0
        logger.info("[MicrosoftTokens] loaded tokens from disk")
        return True

    def _parse_blob(
        self, blob: bytes, path: str,
    ) -> dict[str, Any] | None:

        """Parse blob."""
        if self._secure_store.is_encrypted(blob):
            try:
                return self._secure_store.decrypt_payload(blob)
            except SecureTokenStoreError as e:
                logger.warning(
                    "[MicrosoftTokens] decrypt failed for "
                    "%s: %s", path, e,
                )
                return None
        logger.info(
            "[MicrosoftTokens] reading legacy plaintext token "
            "file at %s — will encrypt on next save",
            path,
        )
        if self._bus is not None:
            emit_legacy_plaintext_detected(
                self._bus, "microsoft", path,
            )
        try:
            return cast(
                "dict[str, Any] | None",
                json.loads(blob.decode("utf-8")),
            )
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning(
                "[MicrosoftTokens] legacy JSON parse "
                "failed: %s", e,
            )
            return None
    async def save(self) -> bool:
        """Save."""
        if (
            self._ms_access_token is None
            and self._ms_refresh_token is None
        ):
            return True
        path = str(await asyncio.to_thread(lambda: Path(self._config.token_file).expanduser()))
        payload = {
            "access_token": self._ms_access_token,
            "refresh_token": self._ms_refresh_token,
            "saved_at": self._token_saved_at,
            "scope": self._config.scope,
        }
        try:
            blob = self._secure_store.encrypt_payload(payload)
        except SecureTokenStoreError:
            logger.exception(
                "[MicrosoftTokens] cannot encrypt tokens "
                "— refusing to write plaintext fallback",
            )
            return False
        ok = await asyncio.to_thread(
            _write_atomic_0600, path, blob,
        )
        await self._emit_permissions_after_save(ok, path)
        return ok
    async def _emit_permissions_after_save(
        self, ok: bool, path: str,
    ) -> None:
        """Emit permissions after save."""
        if not ok:
            return
        def _stat() -> int | None:
            """Stat."""
            try:
                return Path(path).stat().st_mode & 0o7777
            except OSError:
                return None
        mode = await asyncio.to_thread(_stat)
        if mode is not None and self._bus is not None:
            emit_permissions_check(
                self._bus, "microsoft", path, mode,
            )

    async def clear(self) -> None:

        """Clear."""
        self._ms_access_token = None
        self._ms_refresh_token = None
        self._token_saved_at = 0.0
        path = str(await asyncio.to_thread(lambda: Path(self._config.token_file).expanduser()))
        if await asyncio.to_thread(lambda: Path(path).is_file()):
            def _remove_sync() -> None:
                """Remove sync."""
                try:
                    Path(path).unlink()
                except OSError as e:
                    logger.warning(
                        "[MicrosoftTokens] clear: could not "
                        "remove %s: %s", path, e,
                    )
            await asyncio.to_thread(_remove_sync)
def _write_atomic_0600(path: str, blob: bytes) -> bool:
    """Write atomic 0600."""
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
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
        except Exception:
            if fd >= 0:
                os.close(fd)
            raise
        os.replace(tmp, path)
        return True
    except OSError as e:
        logger.warning(
            "[MicrosoftTokens] save failed: %s", e,
        )
        return False
