"""manager.py — Public ``GOGTokenManager`` surface.

# OP-52a | py_modules/unifideck/stores/gog/tokens/manager.py | Depends: (none)
"""
from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from ....security import SecureTokenStore
from .gogdl_credentials import _GogdlCreds
from .oauth import _TokenOAuth
from .storage import _TokenStorage
from .user_info import GOGUserInfo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ..config import GOGConfig

logger = logging.getLogger(__name__)


class GOGTokenManager:
    """GOG token manager."""

    def __init__(
        self,
        config: GOGConfig,
        secure_store: SecureTokenStore | None = None,
        bus: Any = None,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._bus = bus
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._user_info: GOGUserInfo = GOGUserInfo()
        self._token_minted_at: float = 0.0
        self._storage = _TokenStorage(
            config=config, bus=bus,
            secure_store=secure_store or SecureTokenStore(bus=bus),
        )
        self._oauth = _TokenOAuth(
            config=config, save_callback=self._save_oauth_tokens,
        )
        self._gogdl_creds = _GogdlCreds(config=config)

    @property
    def access_token(self) -> str | None:
        """Access token."""
        return self._access_token

    @property
    def refresh_token(self) -> str | None:
        """Refresh token."""
        return self._refresh_token

    @property
    def user_info(self) -> GOGUserInfo:
        """User info."""
        return self._user_info

    @property
    def has_tokens(self) -> bool:
        """Has tokens."""
        return bool(self._access_token and self._refresh_token)

    def get_token_age_seconds(self) -> float:
        """Get token age seconds."""
        if not self._token_minted_at:
            return float('inf')
        return time.time() - self._token_minted_at

    async def load(self) -> bool:
        """Load."""
        loaded = await self._storage.load()
        if loaded is None:
            return False
        access, refresh, info = loaded
        self._access_token = access
        self._refresh_token = refresh
        self._user_info = info
        self._token_minted_at = time.time()
        return True

    async def save(self, access_token: str, refresh_token: str) -> bool:
        """Save."""
        return await self._save_oauth_tokens(access_token, refresh_token)

    async def clear(self) -> None:
        """Clear."""
        self._access_token = None
        self._refresh_token = None
        self._user_info = GOGUserInfo()
        self._token_minted_at = 0.0
        await self._storage.clear_files()

    async def exchange_code(self, auth_code: str) -> bool:
        """Exchange code."""
        ok = await self._oauth.exchange_code(auth_code)
        if not ok:
            return False
        await self._refresh_user_info()
        return True

    async def refresh_if_stale(self) -> bool:
        """Refresh if stale."""
        ok = await self._oauth.refresh_if_stale(
            access_token=self._access_token,
            refresh_token=self._refresh_token,
            age_seconds=self.get_token_age_seconds(),
        )
        if not ok:
            return False
        if self.get_token_age_seconds() < self._config.token_refresh_threshold_seconds:
            return True
        await self._refresh_user_info()
        return True

    @contextlib.asynccontextmanager
    async def gogdl_credentials(self) -> AsyncIterator[dict[str, str]]:
        """Gogdl credentials."""
        env, cleanup = await self.acquire_gogdl_creds()
        try:
            yield env
        finally:
            try:
                await cleanup()
            except Exception as e:
                logger.debug('[GOGTokenManager] cleanup: %s', e)

    async def acquire_gogdl_creds(self) -> tuple[dict[str, str], Any]:
        """Acquire GOGDL creds."""
        await self.refresh_if_stale()
        if not self._access_token or not self._refresh_token:
            raise RuntimeError('no_tokens')
        return await self._gogdl_creds.acquire(
            self._access_token, self._refresh_token,
        )

    async def _save_oauth_tokens(
        self, access_token: str, refresh_token: str,
    ) -> bool:
        """Save oauth tokens."""
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_minted_at = time.time()
        return await self._storage.persist(
            access_token, refresh_token, self._user_info,
        )

    async def _refresh_user_info(self) -> None:
        """Refresh user info."""
        if not self._access_token:
            return
        self._user_info = await self._oauth.fetch_user_info(
            self._access_token, self._user_info,
        )
        await self._storage.persist(
            self._access_token, self._refresh_token or '', self._user_info,
        )
