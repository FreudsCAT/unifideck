"""Tests for ``fetch_json_get`` transient-vs-definitive classification.

The GOG token exchange relies on ``fetch_json_get`` telling a
*transient* network failure (DNS/connect/timeout — the request never
reached the server, so a retry is safe) apart from a *definitive*
outcome (any HTTP status incl. 4xx, or a malformed body — retrying is
pointless). This split is opt-in via ``raise_on_transient`` so the
non-auth callers keep their historical "return None on anything"
behaviour.
"""

from __future__ import annotations

import socket
import urllib.error
from typing import Any

import pytest

from unifideck.stores.gog import http as gog_http
from unifideck.stores.gog.http import TransientNetworkError, fetch_json_get


class _FakeResponse:
    """Minimal urlopen response context manager."""

    def __init__(self, status: int, body: bytes) -> None:
        """Initialize the instance."""
        self.status = status
        self._body = body

    def __enter__(self) -> _FakeResponse:
        """Enter."""
        return self

    def __exit__(self, *_a: Any) -> None:
        """Exit."""
        return None

    def read(self) -> bytes:
        """Read."""
        return self._body


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> None:
    """Replace http.urllib.request.urlopen with a scripted fake."""
    def _fake(*_a: Any, **_kw: Any) -> Any:
        if isinstance(behaviour, BaseException):
            raise behaviour
        return behaviour

    monkeypatch.setattr(gog_http.urllib.request, "urlopen", _fake)


async def test_http_error_returns_none_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A definitive HTTP status → None, even with raise_on_transient."""
    err = urllib.error.HTTPError(
        url="https://auth.gog.com/token",
        code=400,
        msg="Bad Request",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    _patch_urlopen(monkeypatch, err)
    result = await fetch_json_get(
        "https://auth.gog.com/token",
        user_agent="t",
        raise_on_transient=True,
    )
    assert result is None


async def test_network_error_raises_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DNS/transport failure raises TransientNetworkError when asked."""
    err = urllib.error.URLError(
        socket.gaierror("Temporary failure in name resolution"),
    )
    _patch_urlopen(monkeypatch, err)
    with pytest.raises(TransientNetworkError):
        await fetch_json_get(
            "https://auth.gog.com/token",
            user_agent="t",
            raise_on_transient=True,
        )


async def test_network_error_returns_none_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default (flag off) preserves the historical return-None behaviour."""
    err = urllib.error.URLError(
        socket.gaierror("Temporary failure in name resolution"),
    )
    _patch_urlopen(monkeypatch, err)
    result = await fetch_json_get(
        "https://embed.gog.com/userData.json",
        user_agent="t",
    )
    assert result is None


async def test_bad_body_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 with non-JSON body → None (definitive, never raises)."""
    _patch_urlopen(monkeypatch, _FakeResponse(200, b"not json{{{"))
    result = await fetch_json_get(
        "https://auth.gog.com/token",
        user_agent="t",
        raise_on_transient=True,
    )
    assert result is None


async def test_ok_returns_parsed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 with valid JSON → the parsed dict."""
    _patch_urlopen(
        monkeypatch, _FakeResponse(200, b'{"access_token": "a"}'),
    )
    result = await fetch_json_get(
        "https://auth.gog.com/token",
        user_agent="t",
        raise_on_transient=True,
    )
    assert result == {"access_token": "a"}
