from __future__ import annotations
import asyncio
import logging
from typing import Any
from urllib.parse import urlencode
from ...core.types import Game, GameTag
from ...utils.locale import (
    get_unifideck_locale,
    get_unifideck_market,
)
from .microsoft_auth import http_get
from .microsoft_config import MicrosoftConfig
logger = logging.getLogger(__name__)
_TITLE_BATCH_SIZE = 20
class MicrosoftCatalogReader:
    """Microsoft catalog reader."""
    def __init__(
        self,
        config: MicrosoftConfig,
        config_manager: Any,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._config_manager = config_manager
    async def fetch_games(self) -> list[Game]:
        """Fetch games."""
        product_ids = await self._fetch_catalog_ids()
        if not product_ids:
            logger.warning(
                "[MicrosoftCatalog] catalog is empty or "
                "unreachable",
            )
            return []
        titles = await self._batch_get_titles(product_ids)
        games: list[Game] = [
            Game(
                app_id=0,
                store="microsoft",
                store_game_id=pid,
                title=titles.get(pid, pid),
                installed=False,
                tags=[GameTag.XCLOUD],
            )
            for pid in product_ids
        ]
        logger.info(
            "[MicrosoftCatalog] built %d games (%d titles "
            "resolved)",
            len(games), len(titles),
        )
        return games

    async def _fetch_catalog_ids(self) -> list[str]:

        """Fetch catalog ids."""
        locale = get_unifideck_locale(self._config_manager)
        market = get_unifideck_market(self._config_manager)
        base_url = self._config.xcloud_catalog_url
        separator = "&" if "?" in base_url else "?"
        url = (
            f"{base_url}{separator}"
            f"{urlencode({'language': locale, 'market': market})}"
        )
        headers = {
            "User-Agent": self._config.catalog_user_agent,
        }
        try:
            data = await (
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: http_get(url, headers),
                )
            )
        except Exception as e:
            logger.error(
                "[MicrosoftCatalog] catalog fetch failed: "
                "%s", e,
            )
            return []
        if not isinstance(data, list):
            logger.warning(
                "[MicrosoftCatalog] catalog returned %s, "
                "not list",
                type(data).__name__,
            )
            return []
        ids = [
            item["id"]
            for item in data
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"]
        ]
        logger.info(
            "[MicrosoftCatalog] %d IDs from catalog",
            len(ids),
        )
        return ids
    async def _batch_get_titles(
        self, product_ids: list[str],
    ) -> dict[str, str]:
        """Batch get titles."""
        if not product_ids:
            return {}
        locale = get_unifideck_locale(self._config_manager)
        market = get_unifideck_market(self._config_manager)
        base_url = self._config.xcloud_titles_url
        ua = self._config.catalog_user_agent
        result: dict[str, str] = {}
        total_batches = (
            (len(product_ids) + _TITLE_BATCH_SIZE - 1)
            // _TITLE_BATCH_SIZE
        )
        for i in range(0, len(product_ids), _TITLE_BATCH_SIZE):
            batch = product_ids[i: i + _TITLE_BATCH_SIZE]
            batch_result = await self._fetch_one_title_batch(
                batch, base_url, locale, market, ua,
            )
            result.update(batch_result)
        logger.info(
            "[MicrosoftCatalog] resolved %d/%d titles across "
            "%d batches",
            len(result), len(product_ids), total_batches,
        )
        return result

    async def _fetch_one_title_batch(
        self,
        batch: list[str],
        base_url: str,
        locale: str,
        market: str,
        user_agent: str,
    ) -> dict[str, str]:

        """Fetch one title batch."""
        ids_param = ",".join(batch)
        separator = "&" if "?" in base_url else "?"
        params = {
            "bigIds": ids_param,
            "market": market,
            "languages": locale,
            "fieldsTemplate": "Browse",
        }
        url = f"{base_url}{separator}{urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "User-Agent": user_agent,
            "MS-CV": "unifideck.xcloud",
        }
        try:
            data = await (
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: http_get(url, headers),
                )
            )
        except Exception as e:
            logger.warning(
                "[MicrosoftCatalog] title batch failed: %s",
                e,
            )
            return {}
        return self._extract_titles(data)
    @staticmethod
    def _extract_titles(data: Any) -> dict[str, str]:
        """Extract titles."""
        if not isinstance(data, dict):
            return {}
        products = data.get("Products")
        if not isinstance(products, list):
            return {}
        result: dict[str, str] = {}
        for product in products:
            entry = (
                MicrosoftCatalogReader
                ._extract_one_product_title(product)
            )
            if entry is not None:
                pid, title = entry
                result[pid] = title
        return result
    @staticmethod
    def _extract_one_product_title(
        product: Any,
    ) -> tuple[str, str] | None:
        """Extract one product title."""
        if not isinstance(product, dict):
            return None
        pid = product.get("ProductId")
        if not isinstance(pid, str) or not pid:
            return None
        localized = product.get("LocalizedProperties")
        if not isinstance(localized, list):
            return None
        title = (
            MicrosoftCatalogReader
            ._first_localized_title(localized)
        )
        if title is None:
            return None
        return pid, title

    @staticmethod
    def _first_localized_title(
        localized: list,
    ) -> str | None:
        """First localized title."""
        for loc in localized:
            if not isinstance(loc, dict):
                continue
            title = loc.get("ProductTitle")
            if isinstance(title, str) and title:
                return title
        return None