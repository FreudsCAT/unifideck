from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from unifideck.stores.microsoft.microsoft_auth import (
    _log_xsts_xerr,
    build_xbl_chain,
    request_xsts_token,
)

if TYPE_CHECKING:
    from unifideck.stores.microsoft.microsoft_config import MicrosoftConfig
logger = logging.getLogger(__name__)
@dataclass
class XBLTokenChain:
    """Xbltoken chain."""
    xsts_token: str
    user_hash: str
    xuid: str | None = None
    xbl_token: str | None = None
class XBLChainMixin:
    """Xblchain mixin."""
    _ms_access_token: str | None
    _config: MicrosoftConfig
    _locale_fn: Callable[[], str]
    async def build_chain(self) -> XBLTokenChain | None:
        """Build chain."""
        if not self._ms_access_token:
            return None
        access_token = self._ms_access_token
        try:
            result = await (
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: build_xbl_chain(
                        access_token,
                        self._locale_fn(),
                        xbl_auth_url=self._config.xbl_auth_url,
                        xsts_url=self._config.xsts_url,
                        xbl_user_agent=(
                            self._config.xbl_user_agent
                        ),
                    ),
                )
            )
        except Exception:
            logger.exception("[MicrosoftTokens] XBL chain error")
            return None
        if not result:
            return None
        return XBLTokenChain(
            xsts_token=result["xsts_token"],
            user_hash=result["user_hash"],
            xuid=result.get("xuid"),
            xbl_token=result.get("xbl_token"),
        )

    async def build_gssv_chain(
        self,
        xbl_token: str | None = None,
    ) -> XBLTokenChain | None:

        """Build gssv chain."""
        relying_party = self._config.gssv_relying_party
        if xbl_token:
            return await self._gssv_from_xbl_token(
                xbl_token, relying_party,
            )
        return await self._gssv_from_scratch(relying_party)
    async def _gssv_from_xbl_token(
        self, xbl_token: str, relying_party: str,
    ) -> XBLTokenChain | None:
        """Gssv from XBL token."""
        logger.info(
            "[MicrosoftTokens] requesting GSSV XSTS "
            "(rp=%s)", relying_party,
        )
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: request_xsts_token(
                    xbl_token=xbl_token,
                    xsts_rp=relying_party,
                    locale=self._locale_fn(),
                    xsts_url=self._config.xsts_url,
                    xbl_user_agent=self._config.xbl_user_agent,
                ),
            )
        except Exception:
            logger.exception("[MicrosoftTokens] GSSV XSTS error")
            return None
        if not resp:
            logger.error(
                "[MicrosoftTokens] GSSV XSTS empty response",
            )
            return None
        if "XErr" in resp:
            xerr = resp.get("XErr")
            logger.error(
                "[MicrosoftTokens] GSSV XSTS XErr=%s "
                "(rp=%s, full=%.300s)",
                xerr, relying_party, repr(resp),
            )
            if isinstance(xerr, int):
                _log_xsts_xerr(xerr)
            return None
        xsts_token = resp.get("Token")
        if not xsts_token:
            logger.error(
                "[MicrosoftTokens] GSSV XSTS missing Token "
                "(resp=%.300s)", repr(resp),
            )
            return None
        claims = resp.get("DisplayClaims", {}).get("xui", [{}])
        user_hash = claims[0].get("uhs") if claims else None
        if not user_hash:
            logger.error(
                "[MicrosoftTokens] GSSV XSTS missing user_hash "
                "(claims=%.300s)", repr(claims),
            )
            return None
        logger.info(
            "[MicrosoftTokens] ✓ GSSV chain built (uhs=%s)",
            user_hash,
        )
        return XBLTokenChain(
            xsts_token=xsts_token,
            user_hash=user_hash,
            xuid=claims[0].get("xid") if claims else None,
            xbl_token=xbl_token,
        )
    async def _gssv_from_scratch(
        self, relying_party: str,
    ) -> XBLTokenChain | None:
        """Gssv from scratch."""
        if not self._ms_access_token:
            logger.error(
                "[MicrosoftTokens] GSSV from-scratch: no "
                "_ms_access_token (login state lost)",
            )
            return None
        logger.info(
            "[MicrosoftTokens] building GSSV chain from "
            "scratch (rp=%s)", relying_party,
        )
        access_token = self._ms_access_token
        try:
            result = await (
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: build_xbl_chain(
                        access_token,
                        self._locale_fn(),
                        xbl_auth_url=self._config.xbl_auth_url,
                        xsts_url=self._config.xsts_url,
                        xbl_user_agent=(
                            self._config.xbl_user_agent
                        ),
                        xsts_relying_party=relying_party,
                    ),
                )
            )
        except Exception:
            logger.exception("[MicrosoftTokens] GSSV chain error")
            return None
        if not result:
            return None
        return XBLTokenChain(
            xsts_token=result["xsts_token"],
            user_hash=result["user_hash"],
            xuid=result.get("xuid"),
            xbl_token=result.get("xbl_token"),
        )
