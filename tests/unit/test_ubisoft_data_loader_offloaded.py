"""Regression: Ubisoft `_DataLoader` must not block the event loop.

`load_configurations` / `load_ownership_set` / `load_ownership_uuids` used
to call the synchronous `_find_library_configurations_path` /
`_discover_ownership_file` directly on the event loop. Both walk Wine
prefix directories on disk (`Path.is_dir()`, `iterdir()`) — on an SD
card or a cold cache this can block for a noticeable stretch, freezing
every other coroutine (including UI-facing RPCs) for the duration. They
are now wrapped in `asyncio.to_thread`, moving the blocking disk walk
off the event loop onto the default thread pool.

This covers both angles: correctness (results are unchanged with the
sync methods mocked) and the offload itself (via a patched
`asyncio.to_thread` spy, and a live blocking-vs-concurrent-progress
demonstration).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from unifideck.stores.ubisoft.library.data_loader import _DataLoader

_SPACE = "abcd1234-5678-90ab-cdef-1234567890ab"


def _loader() -> _DataLoader:
    """A `_DataLoader` with no real config/paths — the tests here only
    exercise methods that go through the mocked/monkeypatched sync
    helpers, so the constructor's real collaborators are never touched.
    """
    return _DataLoader(config=object(), paths=object())  # type: ignore[arg-type]


# ── correctness: results unchanged with the sync method mocked ─────


@pytest.mark.asyncio
async def test_load_configurations_returns_parsed_configs(monkeypatch):
    loader = _loader()
    monkeypatch.setattr(
        loader, "_find_library_configurations_path", lambda: "/fake/configs.bin",
    )

    sentinel = [object()]

    def _fake_parse(path: str) -> list[object]:
        assert path == "/fake/configs.bin"
        return sentinel

    result = await loader.load_configurations(_fake_parse)
    assert result is sentinel


@pytest.mark.asyncio
async def test_load_configurations_none_when_no_binary_found(monkeypatch):
    loader = _loader()
    monkeypatch.setattr(loader, "_find_library_configurations_path", lambda: None)

    called = False

    def _fake_parse(_path: str) -> list[object]:
        nonlocal called
        called = True
        return []

    result = await loader.load_configurations(_fake_parse)
    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_load_ownership_set_returns_parsed_ids(monkeypatch):
    loader = _loader()
    monkeypatch.setattr(
        loader,
        "_discover_ownership_file",
        lambda: ("/fake/ownership.bin", "user123"),
    )

    def _fake_parse(path: str) -> list[int]:
        assert path == "/fake/ownership.bin"
        return [1, 2, 2, 3]

    result = await loader.load_ownership_set(_fake_parse)
    assert result == {1, 2, 3}


@pytest.mark.asyncio
async def test_load_ownership_set_none_when_no_file(monkeypatch):
    loader = _loader()
    monkeypatch.setattr(loader, "_discover_ownership_file", lambda: (None, ""))

    result = await loader.load_ownership_set(lambda _path: [1])
    assert result is None


@pytest.mark.asyncio
async def test_load_ownership_uuids_returns_uuid_set(monkeypatch):
    loader = _loader()
    monkeypatch.setattr(
        loader,
        "_discover_ownership_file",
        lambda: ("/fake/ownership.bin", "user123"),
    )
    monkeypatch.setattr(
        "unifideck.stores.ubisoft.parser.parse_ownership_uuids",
        lambda path: [_SPACE] if path == "/fake/ownership.bin" else [],
    )

    result = await loader.load_ownership_uuids()
    assert result == {_SPACE}


@pytest.mark.asyncio
async def test_load_ownership_uuids_empty_when_no_file(monkeypatch):
    loader = _loader()
    monkeypatch.setattr(loader, "_discover_ownership_file", lambda: (None, ""))

    result = await loader.load_ownership_uuids()
    assert result == set()


# ── offload: the sync method runs via asyncio.to_thread, not inline ─


@pytest.mark.asyncio
async def test_load_configurations_offloads_via_to_thread(monkeypatch):
    loader = _loader()
    sync_calls: list[str] = []

    def _fake_find_path() -> str:
        sync_calls.append("find_path")
        return "/fake/configs.bin"

    monkeypatch.setattr(loader, "_find_library_configurations_path", _fake_find_path)

    to_thread_targets: list[object] = []
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):
        to_thread_targets.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy_to_thread)

    result = await loader.load_configurations(lambda _path: ["cfg"])

    assert result == ["cfg"]
    assert loader._find_library_configurations_path in to_thread_targets
    assert sync_calls == ["find_path"]


@pytest.mark.asyncio
async def test_load_ownership_set_offloads_discover_via_to_thread(monkeypatch):
    loader = _loader()

    def _fake_discover() -> tuple[str | None, str]:
        return ("/fake/ownership.bin", "user123")

    monkeypatch.setattr(loader, "_discover_ownership_file", _fake_discover)

    to_thread_targets: list[object] = []
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):
        to_thread_targets.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy_to_thread)

    await loader.load_ownership_set(lambda _path: [1])

    assert loader._discover_ownership_file in to_thread_targets


@pytest.mark.asyncio
async def test_load_ownership_uuids_offloads_discover_via_to_thread(monkeypatch):
    loader = _loader()
    monkeypatch.setattr(
        loader, "_discover_ownership_file", lambda: (None, ""),
    )

    to_thread_targets: list[object] = []
    real_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):
        to_thread_targets.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy_to_thread)

    await loader.load_ownership_uuids()

    assert loader._discover_ownership_file in to_thread_targets


@pytest.mark.asyncio
async def test_load_configurations_does_not_block_event_loop():
    """Live demonstration (no mocked `to_thread`): a slow, blocking
    `_find_library_configurations_path` must not stall a concurrently
    running coroutine's progress — the whole point of the offload.

    A ticker coroutine increments a counter every 10ms via
    `asyncio.sleep`. If `load_configurations` blocked the loop (the
    pre-fix behaviour of calling the blocking function inline), the
    ticker would accumulate zero extra ticks while it ran. With the
    `asyncio.to_thread` offload, the loop stays free to keep ticking.
    """
    loader = _loader()
    ticks = 0
    stop = False

    async def _ticker() -> None:
        nonlocal ticks
        while not stop:
            await asyncio.sleep(0.01)
            ticks += 1

    def _blocking_find_path() -> str:
        # Simulate a slow disk walk (SD card / cold cache) with a
        # real, uninterruptible blocking sleep — the same kind of
        # call `time.sleep` inside `Path.iterdir()` would produce.
        time.sleep(0.3)
        return "/fake/configs.bin"

    loader._find_library_configurations_path = _blocking_find_path  # type: ignore[method-assign]

    ticker_task = asyncio.create_task(_ticker())
    await loader.load_configurations(lambda _path: ["cfg"])
    stop = True
    await ticker_task

    # 0.3s of blocking work at a 10ms tick period should yield ~30
    # ticks if the loop stayed free; a inline blocking call would
    # have produced 0 (or near-0, if it landed before the sleep).
    assert ticks >= 10
