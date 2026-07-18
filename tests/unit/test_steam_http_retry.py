"""Shared Steam-Store HTTP retry helper + rate-limit gate.

``get_json_with_backoff`` centralises the 429 backoff that used to be
duplicated in storesearch/appdetails (and absent from appreviews);
``RateLimitGate`` makes one 429 pause every in-flight worker so the
raised concurrency cap degrades into a collective pause instead of a
retry storm.
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from unifideck.steam.appreviews import _request_appreviews
from unifideck.steam.http_retry import (
    RateLimitGate,
    get_json_with_backoff,
)


class _FakeResponse:
    def __init__(
        self,
        status: int,
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._payload = payload
        self.headers = headers or {}

    async def json(self, content_type: str | None = None) -> Any:
        return self._payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSession:
    """Yields queued responses; records call count."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.calls = 0

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls += 1
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_retries_429_then_returns_payload() -> None:
    sess = _FakeSession([
        _FakeResponse(429, headers={"Retry-After": "1"}),
        _FakeResponse(200, payload={"ok": True}),
    ])
    with patch(
        "unifideck.steam.http_retry.asyncio.sleep", new=AsyncMock(),
    ) as sleep:
        payload = await get_json_with_backoff(
            sess, "https://x", timeout_s=5, log_tag="[test]",  # type: ignore[arg-type]
        )
    assert payload == {"ok": True}
    assert sess.calls == 2
    sleep.assert_awaited_once()
    # Retry-After honored (1s) + jitter (< 0.5s)
    delay = sleep.await_args.args[0]
    assert 1.0 <= delay < 1.5


@pytest.mark.asyncio
async def test_retry_after_clamped_to_cap() -> None:
    sess = _FakeSession([
        _FakeResponse(429, headers={"Retry-After": "99999"}),
        _FakeResponse(200, payload={}),
    ])
    with patch(
        "unifideck.steam.http_retry.asyncio.sleep", new=AsyncMock(),
    ) as sleep:
        await get_json_with_backoff(
            sess, "https://x", timeout_s=5, log_tag="[test]",  # type: ignore[arg-type]
        )
    assert sleep.await_args.args[0] < 31.0  # hostile header can't park us


@pytest.mark.asyncio
async def test_exhausted_retries_return_none() -> None:
    sess = _FakeSession([_FakeResponse(429) for _ in range(5)])
    with patch("unifideck.steam.http_retry.asyncio.sleep", new=AsyncMock()):
        payload = await get_json_with_backoff(
            sess, "https://x", timeout_s=5, log_tag="[test]",  # type: ignore[arg-type]
            max_retries=2,
        )
    assert payload is None
    assert sess.calls == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_non_200_returns_none_without_retry() -> None:
    sess = _FakeSession([_FakeResponse(404)])
    payload = await get_json_with_backoff(
        sess, "https://x", timeout_s=5, log_tag="[test]",  # type: ignore[arg-type]
    )
    assert payload is None
    assert sess.calls == 1


@pytest.mark.asyncio
async def test_gate_trip_delays_concurrent_waiter() -> None:
    gate = RateLimitGate()
    gate.trip(0.1)
    start = time.monotonic()
    await gate.wait()
    assert time.monotonic() - start >= 0.09  # paused for the window


@pytest.mark.asyncio
async def test_gate_is_noop_when_open() -> None:
    gate = RateLimitGate()
    start = time.monotonic()
    await gate.wait()
    assert time.monotonic() - start < 0.05


class _RecordingGate(RateLimitGate):
    """Records trips without arming the deadline (keeps ``wait`` a no-op
    while the test mocks ``asyncio.sleep`` — a real deadline would make
    the wait loop spin forever under a no-op sleep)."""

    def __init__(self) -> None:
        super().__init__()
        self.tripped: list[float] = []

    def trip(self, delay_s: float) -> None:
        self.tripped.append(delay_s)


@pytest.mark.asyncio
async def test_429_trips_the_shared_gate() -> None:
    gate = _RecordingGate()
    sess = _FakeSession([
        _FakeResponse(429, headers={"Retry-After": "5"}),
        _FakeResponse(200, payload={}),
    ])
    with patch("unifideck.steam.http_retry.asyncio.sleep", new=AsyncMock()):
        await get_json_with_backoff(
            sess, "https://x", timeout_s=5, log_tag="[test]",  # type: ignore[arg-type]
            gate=gate,
        )
    # The 429 told every sibling worker to pause for the window.
    assert len(gate.tripped) == 1
    assert 5.0 <= gate.tripped[0] < 5.5  # Retry-After + jitter


@pytest.mark.asyncio
async def test_appreviews_now_retries_on_429() -> None:
    # appreviews previously had NO rate-limit handling — a 429 during
    # a bulk sync silently dropped the review data.
    payload = {
        "success": 1,
        "query_summary": {
            "review_score": 8, "total_positive": 90, "total_reviews": 100,
        },
    }
    sess = _FakeSession([
        _FakeResponse(429, headers={"Retry-After": "1"}),
        _FakeResponse(200, payload=payload),
    ])
    with patch("unifideck.steam.http_retry.asyncio.sleep", new=AsyncMock()):
        result = await _request_appreviews(
            sess, 945360, "https://x", {}, 5,  # type: ignore[arg-type]
        )
    assert result == {
        "review_score": 8, "review_percentage": 90, "total_reviews": 100,
    }
    assert sess.calls == 2
