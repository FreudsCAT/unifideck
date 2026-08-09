"""nile invocations must never overlap — concurrency corrupts its config.

Observed on a real device: two concurrent ``nile install --info`` size
lookups left ``~/.config/nile/user.json`` at 4621 bytes with valid JSON
through byte 4620 and one trailing ``}`` — a short write landing over a
longer file. nile refreshes its token opportunistically and rewrites that
file non-atomically, so overlapping processes race on it.

Every later nile call then died before doing any work::

    File "nile/api/authorization.py", line 164, in is_logged_in
    json.decoder.JSONDecodeError: Extra data: line 1 column 4621

presenting as Amazon logged-out, failed installs, and ``get_url_failed``
from the auth flow.

These tests pin the serialisation for the short commands that fire in
bursts (size probes) and the auth commands that a user can trigger while a
background walk is already running — the exact overlap that broke it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from unifideck.stores.amazon import amazon_updates
from unifideck.stores.amazon.nile_lock import nile_cli_lock


class _Tracker:
    """Records peak concurrency across faked nile subprocess spawns."""

    def __init__(self) -> None:
        self.live = 0
        self.peak = 0

    def spawn(self, stdout: bytes = b'{"download_size": 123}'):
        async def _exec(*_a: Any, **_kw: Any) -> Any:
            self.live += 1
            self.peak = max(self.peak, self.live)

            async def communicate() -> tuple[bytes, bytes]:
                await asyncio.sleep(0.02)
                self.live -= 1
                return stdout, b""
            return SimpleNamespace(returncode=0, communicate=communicate)
        return _exec


def _updates(size_timeout: int = 30) -> Any:
    return amazon_updates.AmazonUpdateChecker(
        bus=SimpleNamespace(),
        cli_path="/bin/nile",
        library=SimpleNamespace(),
        list_updates_timeout=30,
        get_size_timeout=size_timeout,
        default_install_root="/games",
    )


def test_lock_is_process_wide() -> None:
    """One nile config file per user, so one lock for the whole process."""
    assert nile_cli_lock() is nile_cli_lock()


@pytest.mark.asyncio
async def test_size_probes_never_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _Tracker()
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", tracker.spawn(),
    )
    checker = _updates()

    sizes = await asyncio.gather(
        *(checker.get_game_size(f"g{i}") for i in range(5)),
    )

    assert sizes == [123] * 5
    assert tracker.peak == 1, "two nile processes must never run at once"


@pytest.mark.asyncio
async def test_lock_is_released_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashing nile must not wedge every later Amazon operation."""
    async def _boom(*_a: Any, **_kw: Any) -> Any:
        raise OSError("nile exploded")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

    assert await _updates().get_game_size("g1") is None
    assert not nile_cli_lock().locked()


@pytest.mark.asyncio
async def test_lock_is_released_after_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _hang(*_a: Any, **_kw: Any) -> Any:
        async def communicate() -> tuple[bytes, bytes]:
            await asyncio.sleep(30)
            return b"", b""
        return SimpleNamespace(returncode=0, communicate=communicate)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _hang)
    checker = _updates(size_timeout=1)

    assert await checker.get_game_size("g1") is None
    assert not nile_cli_lock().locked()
