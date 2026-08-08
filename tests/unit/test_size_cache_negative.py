"""A size lookup that fails must not be re-paid on every page open.

Measured on a real device after the warm-up had run to completion: 472 of
608 games had a size, and the other 136 failed EVERY time — Amazon because
nile's ``user.json`` had become unparseable, so every invocation crashed
before doing any work. Nothing recorded those failures, so each of those
games re-ran the full multi-second store lookup on every single App-Details
open and still displayed nothing. That is the "Space Required takes five
seconds and never gets better" report.

Recording the failure with a TTL makes the second open instant while still
letting a repaired login or a transient outage recover on its own.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from unifideck.services import size_cache
from unifideck.services.size_cache import SizeCache


@pytest.fixture
def cache_path(tmp_path: Path) -> str:
    return str(tmp_path / "game_sizes.json")


# ── negative caching ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_is_not_set_by_default(cache_path: str) -> None:
    assert await SizeCache(cache_path).is_unknown("gog", "1") is False


@pytest.mark.asyncio
async def test_marked_unknown_is_reported(cache_path: str) -> None:
    c = SizeCache(cache_path)
    await c.mark_unknown("gog", "1")
    assert await c.is_unknown("gog", "1") is True
    assert await c.is_unknown("gog", "2") is False


@pytest.mark.asyncio
async def test_unknown_expires(cache_path: str, monkeypatch) -> None:
    """A repaired store login must heal without user action."""
    c = SizeCache(cache_path)
    await c.mark_unknown("amazon", "x")
    assert await c.is_unknown("amazon", "x") is True

    monkeypatch.setattr(size_cache, "UNKNOWN_TTL_S", -1)
    assert await c.is_unknown("amazon", "x") is False


@pytest.mark.asyncio
async def test_unknown_survives_a_restart(cache_path: str) -> None:
    """Plugin restarts are frequent (notably right after a sync)."""
    await SizeCache(cache_path).mark_unknown("gog", "1")
    assert await SizeCache(cache_path).is_unknown("gog", "1") is True


@pytest.mark.asyncio
async def test_a_real_size_clears_the_failure_stamp(cache_path: str) -> None:
    c = SizeCache(cache_path)
    await c.mark_unknown("gog", "1")
    await c.put("gog", "1", 4242)

    assert await c.get("gog", "1") == 4242
    assert await c.is_unknown("gog", "1") is False


# ── format compatibility ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_flat_file_still_loads(cache_path: str) -> None:
    """The device already holds a warm 472-entry cache in the old shape —
    the new format must not throw it away."""
    Path(cache_path).write_text(json.dumps({"gog:1": 111, "epic:2": 222}))
    c = SizeCache(cache_path)

    assert await c.get("gog", "1") == 111
    assert await c.get("epic", "2") == 222
    assert await c.is_unknown("gog", "1") is False


@pytest.mark.asyncio
async def test_legacy_file_is_preserved_on_first_write(cache_path: str) -> None:
    Path(cache_path).write_text(json.dumps({"gog:1": 111}))
    c = SizeCache(cache_path)
    await c.put("epic", "2", 222)

    reloaded = SizeCache(cache_path)
    assert await reloaded.get("gog", "1") == 111
    assert await reloaded.get("epic", "2") == 222


@pytest.mark.asyncio
async def test_corrupt_file_degrades_to_empty(cache_path: str) -> None:
    Path(cache_path).write_text("{ not json")
    c = SizeCache(cache_path)
    assert await c.get("gog", "1") is None
    assert await c.is_unknown("gog", "1") is False


@pytest.mark.asyncio
async def test_non_mapping_sections_are_ignored(cache_path: str) -> None:
    """The file is user-writable; a hand-edit must not crash the plugin."""
    Path(cache_path).write_text(json.dumps({"sizes": "nope", "unknown": 7}))
    c = SizeCache(cache_path)
    assert await c.get("gog", "1") is None
    assert await c.is_unknown("gog", "1") is False


@pytest.mark.asyncio
async def test_non_positive_sizes_are_never_stored(cache_path: str) -> None:
    c = SizeCache(cache_path)
    await c.put("gog", "1", 0)
    await c.put("gog", "2", -5)
    assert await c.get("gog", "1") is None
    assert await c.get("gog", "2") is None
