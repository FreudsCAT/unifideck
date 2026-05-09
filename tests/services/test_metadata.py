"""Tests for services/metadata_service.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from unifideck.services.metadata_service import MetadataService

@pytest.fixture
def bus():
    return AsyncMock()

@pytest.fixture
def cache():
    return MagicMock()

@pytest.fixture
def service(bus, cache):
    return MetadataService(bus, cache)

@pytest.mark.asyncio
async def test_resolve_metadata_merges_sources(service, cache):
    # Mock individual fetchers
    service._fetch_steam_store = AsyncMock(return_value={"steam_appid": 123, "title": "Game Name"})
    service._fetch_unifidb = AsyncMock(return_value={"description": "A cool game", "genres": ["Action"]})
    service._fetch_metacritic = AsyncMock(return_value={"metacritic_score": 85})
    
    # Mock cache check
    cache.get.return_value = None
    
    game = MagicMock()
    game.store = "epic"
    game.game_id = "xyz"
    game.title = "Original Title"
    game.get = lambda k, d=None: getattr(game, k, d)
    
    metadata = await service.enrich(game)
        
    assert metadata["title"] == "Game Name"
    assert metadata["description"] == "A cool game"
    assert metadata["metacritic_score"] == 85
    assert metadata["genres"] == ["Action"]

@pytest.mark.asyncio
async def test_metacritic_slugification():
    from unifideck.metadata.metacritic import slugify
    assert slugify("Hades II") == "hades-ii"
    assert slugify("The Witcher 3: Wild Hunt") == "the-witcher-3-wild-hunt"
    assert slugify("Game!!! @#$ Name") == "game-name"

@pytest.mark.asyncio
async def test_unifidb_mapping():
    from unifideck.metadata.unifidb import _map_record
    data = {
        "title": "Hades",
        "description": "Roguelike",
        "genres": ["Action", "Indie"],
        "stores": {"epic": "hades-slug"}
    }
    result = _map_record(data, "hades-slug")
    assert result.title == "Hades"
    assert result.stores["epic"] == "hades-slug"
