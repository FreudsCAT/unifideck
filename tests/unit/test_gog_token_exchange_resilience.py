"""Tests for GOG token-exchange network resilience.

Covers the transient-vs-definitive split added to fix a tester's
opaque ``token_exchange_failed`` on a flaky/offline network:

* ``_TokenOAuth.exchange_code`` retries ONLY on a transient network
  failure (``TransientNetworkError``, raised before any HTTP response,
  so the single-use OAuth code was never consumed) and reports the
  outcome as a three-state :class:`ExchangeOutcome`;
* a definitive result (HTTP status → ``None``, bad body, or save
  failure) is NEVER retried — retrying a consumed code only fails
  again;
* ``GOGBrowserAuth._exchange_code`` maps each outcome to the right
  ``AuthResult.error`` so the frontend can distinguish a network
  problem (``token_exchange_network_error``) from a real auth failure
  (``token_exchange_failed``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from unifideck.stores.gog.http import TransientNetworkError
from unifideck.stores.gog.tokens.oauth import (
    _MAX_EXCHANGE_ATTEMPTS,
    ExchangeOutcome,
    _TokenOAuth,
)


def _config() -> SimpleNamespace:
    """Duck-typed GOGConfig with the fields the exchange path reads."""
    return SimpleNamespace(
        client_id="cid",
        client_secret="secret",
        redirect_uri="https://embed.gog.com/on_login_success?origin=client",
        token_url="https://auth.gog.com/token",
        user_agent="unifideck-test",
    )


class _SaveSpy:
    """Records save calls and returns a configurable result."""

    def __init__(self, result: bool = True) -> None:
        """Initialize the instance."""
        self.calls: list[tuple[str, str]] = []
        self._result = result

    async def __call__(self, access: str, refresh: str) -> bool:
        """Call."""
        self.calls.append((access, refresh))
        return self._result


class _FetchStub:
    """Async stand-in for ``fetch_json_get`` driven by a script.

    Each entry is either a callable raising, an exception instance to
    raise, or a value to return. Records how many times it was called.
    """

    def __init__(self, script: list[Any]) -> None:
        """Initialize the instance."""
        self._script = script
        self.calls = 0

    async def __call__(self, *_a: Any, **_kw: Any) -> Any:
        """Call."""
        item = self._script[self.calls]
        self.calls += 1
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make backoff instant so retry tests don't wait on wall-clock."""
    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "unifideck.stores.gog.tokens.oauth.asyncio.sleep", _instant,
    )


def _oauth(
    monkeypatch: pytest.MonkeyPatch,
    script: list[Any],
    *,
    save_ok: bool = True,
) -> tuple[_TokenOAuth, _FetchStub, _SaveSpy]:
    """Build a _TokenOAuth wired to a scripted fetch + save spy."""
    fetch = _FetchStub(script)
    save = _SaveSpy(save_ok)
    monkeypatch.setattr(
        "unifideck.stores.gog.tokens.oauth.fetch_json_get", fetch,
    )
    oauth = _TokenOAuth(config=_config(), save_callback=save)
    return oauth, fetch, save


_GOOD = {"access_token": "a", "refresh_token": "r"}


async def test_transient_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blip on attempt 1 recovers on attempt 2 → OK, save once."""
    oauth, fetch, save = _oauth(
        monkeypatch, [TransientNetworkError("dns"), _GOOD],
    )
    outcome = await oauth.exchange_code("code")
    assert outcome is ExchangeOutcome.OK
    assert fetch.calls == 2
    assert len(save.calls) == 1


async def test_transient_exhausted_maps_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sustained outage → NETWORK_FAILED after N attempts, no save."""
    script = [TransientNetworkError("dns")] * _MAX_EXCHANGE_ATTEMPTS
    oauth, fetch, save = _oauth(monkeypatch, script)
    outcome = await oauth.exchange_code("code")
    assert outcome is ExchangeOutcome.NETWORK_FAILED
    assert fetch.calls == _MAX_EXCHANGE_ATTEMPTS
    assert save.calls == []


async def test_definitive_none_maps_auth_failed_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A definitive HTTP failure (None) → AUTH_FAILED, exactly one call.

    Pins the critical invariant: a consumed/invalid code (HTTP 400
    surfaces as None from fetch_json_get) is NEVER retried.
    """
    oauth, fetch, save = _oauth(monkeypatch, [None])
    outcome = await oauth.exchange_code("code")
    assert outcome is ExchangeOutcome.AUTH_FAILED
    assert fetch.calls == 1
    assert save.calls == []


async def test_bad_body_maps_auth_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 with tokens missing → AUTH_FAILED, one call, no save."""
    oauth, fetch, save = _oauth(monkeypatch, [{"unexpected": "shape"}])
    outcome = await oauth.exchange_code("code")
    assert outcome is ExchangeOutcome.AUTH_FAILED
    assert fetch.calls == 1
    assert save.calls == []


async def test_save_failure_maps_auth_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Good tokens but persist fails → AUTH_FAILED (not network)."""
    oauth, fetch, save = _oauth(monkeypatch, [_GOOD], save_ok=False)
    outcome = await oauth.exchange_code("code")
    assert outcome is ExchangeOutcome.AUTH_FAILED
    assert fetch.calls == 1
    assert len(save.calls) == 1


class _ManagerStub:
    """Token manager whose exchange_code returns a fixed outcome."""

    def __init__(self, outcome: ExchangeOutcome) -> None:
        """Initialize the instance."""
        self._outcome = outcome

    async def exchange_code(self, _code: str) -> ExchangeOutcome:
        """Exchange code."""
        return self._outcome


async def _run_auth_exchange(outcome: ExchangeOutcome) -> Any:
    """Drive GOGBrowserAuth._exchange_code with a stubbed manager."""
    from unifideck.stores.gog.auth import GOGBrowserAuth

    auth = GOGBrowserAuth.__new__(GOGBrowserAuth)
    auth._tokens = _ManagerStub(outcome)  # type: ignore[attr-defined]
    return await auth._exchange_code("code")


async def test_auth_maps_ok_to_success() -> None:
    """OK outcome → successful AuthResult, no error."""
    result = await _run_auth_exchange(ExchangeOutcome.OK)
    assert result.success is True
    assert result.error is None


async def test_auth_maps_network_to_network_error() -> None:
    """NETWORK_FAILED → token_exchange_network_error code."""
    result = await _run_auth_exchange(ExchangeOutcome.NETWORK_FAILED)
    assert result.success is False
    assert result.error == "token_exchange_network_error"


async def test_auth_maps_auth_failed_to_generic() -> None:
    """AUTH_FAILED → the existing token_exchange_failed code."""
    result = await _run_auth_exchange(ExchangeOutcome.AUTH_FAILED)
    assert result.success is False
    assert result.error == "token_exchange_failed"
