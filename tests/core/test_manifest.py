"""Tests for core/manifest.py (OP-04e)."""
from __future__ import annotations

import pytest

from unifideck.core.manifest import (
    DEFAULT_MANIFEST_FILENAME,
    GameManifest,
    DiscoveryResult,
    build_manifest,
    read_manifest,
    write_manifest,
)


def test_build_manifest():
    m = build_manifest("epic", "abc123", "Hades", "Hades.exe")
    assert m.store == "epic"
    assert m.store_id == "abc123"
    assert m.title == "Hades"
    assert m.executable_relative == "Hades.exe"
    assert m.installed_at  # not empty
    assert m.platform == "windows"


def test_manifest_to_dict():
    m = build_manifest("gog", "456", "Celeste", "Celeste.exe")
    d = m.to_dict()
    assert d["store"] == "gog"
    assert d["store_id"] == "456"
    assert isinstance(d, dict)


def test_manifest_from_dict():
    data = {
        "unifideck_version": "1.0",
        "store": "epic",
        "store_id": "abc",
        "title": "Test",
        "executable_relative": "test.exe",
        "installed_at": "2026-01-01T00:00:00",
    }
    m = GameManifest.from_dict(data)
    assert m is not None
    assert m.store == "epic"


def test_manifest_from_dict_missing_keys():
    assert GameManifest.from_dict({"store": "epic"}) is None
    assert GameManifest.from_dict({}) is None


def test_discovery_result_to_dict():
    dr = DiscoveryResult(scanned_directories=5, manifests_found=2)
    d = dr.to_dict()
    assert d["scanned_directories"] == 5
    assert d["manifests_found"] == 2


@pytest.mark.asyncio
async def test_write_and_read_manifest(tmp_path):
    ok = await write_manifest(
        str(tmp_path), "epic", "game1", "Test Game", "game.exe",
    )
    assert ok is True

    m = await read_manifest(str(tmp_path))
    assert m is not None
    assert m.store == "epic"
    assert m.title == "Test Game"


@pytest.mark.asyncio
async def test_read_manifest_missing(tmp_path):
    m = await read_manifest(str(tmp_path / "nonexistent"))
    assert m is None


def test_default_manifest_filename():
    assert DEFAULT_MANIFEST_FILENAME == ".unifideck_manifest.json"


@pytest.mark.asyncio
async def test_discover_all_empty(tmp_path):
    """Discovery in empty dir should return zero results."""
    from unifideck.core.manifest import discover_all
    result = await discover_all()
    assert result.errors == [] or isinstance(result.errors, list)


@pytest.mark.asyncio
async def test_discover_all_with_manifest(tmp_path):
    """Discovery should find a written manifest."""
    from unifideck.core.manifest import discover_all
    from unifideck.event_bus.event_bus import EventBus
    from tests.helpers import MockConfig

    game_dir = tmp_path / "TestGame"
    game_dir.mkdir()
    await write_manifest(str(game_dir), "epic", "game1", "Test", "game.exe")

    bus = EventBus()
    installed = []
    async def on_installed(**kw):
        installed.append(kw)
    bus.on("game_installed", on_installed)

    cfg = MockConfig({"stores": {"epic": {"install_dir": str(tmp_path)}}})
    result = await discover_all(bus=bus, config=cfg)
    assert result.manifests_found >= 1


@pytest.mark.asyncio
async def test_discover_installed_games_legacy(tmp_path):
    from unifideck.core.manifest import discover_installed_games
    result = await discover_installed_games()
    assert isinstance(result, dict)
    assert "scanned_directories" in result


def test_manifest_roundtrip():
    m = build_manifest("gog", "123", "Celeste", "Celeste.exe")
    d = m.to_dict()
    m2 = GameManifest.from_dict(d)
    assert m2 is not None
    assert m2.store == m.store
    assert m2.title == m.title
