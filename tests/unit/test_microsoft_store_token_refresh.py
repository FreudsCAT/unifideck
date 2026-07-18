"""Unit tests for MicrosoftStore's token validity + background refresh.

Regression: ``is_available()`` only checked whether a ``refresh_token``
was present on disk, never whether it actually still worked. A token
Microsoft had already revoked/expired (confirmed live: HTTP 400 on
refresh) still reported ``available: True`` until the next real
library sync exposed it — the QAM showed "logged in" for a session
that was already dead. ``is_available()`` now reuses the same
``refresh_if_stale`` validation ``get_library`` already did.

Also covers the new best-effort background poller
(``start_token_refresh_polling``) that exercises the token
periodically instead of purely on-demand, so a token that simply sits
unused for a while still gets refreshed.
"""
from __future__ import annotations

import asyncio

from unifideck.stores.microsoft.microsoft_store import MicrosoftStore


class _FakeConfig:
    def is_valid(self) -> bool:
        return True


class _FakeTokens:
    def __init__(
        self, *, loaded: bool = True, fresh: bool = True,
    ) -> None:
        self.loaded = loaded
        self.fresh = fresh
        self.cleared = False
        self.load_calls = 0
        self.refresh_calls = 0

    async def load(self) -> bool:
        self.load_calls += 1
        return self.loaded

    async def refresh_if_stale(self) -> bool:
        self.refresh_calls += 1
        return self.fresh

    async def clear(self) -> None:
        self.cleared = True


def _store(tokens: _FakeTokens, *, config_valid: bool = True) -> MicrosoftStore:
    store = MicrosoftStore.__new__(MicrosoftStore)
    store._ms_config = _FakeConfig() if config_valid else _InvalidConfig()
    store._tokens = tokens
    store._poll_task = None
    return store


class _InvalidConfig:
    def is_valid(self) -> bool:
        return False


# ── is_available: now validates, not just checks presence ──────────

async def test_is_available_false_when_config_invalid():
    store = _store(_FakeTokens(), config_valid=False)

    assert await store.is_available() is False


async def test_is_available_false_when_no_token_on_disk():
    tokens = _FakeTokens(loaded=False)
    store = _store(tokens)

    assert await store.is_available() is False
    assert tokens.refresh_calls == 0  # never got that far


async def test_is_available_true_when_token_present_and_fresh():
    tokens = _FakeTokens(loaded=True, fresh=True)
    store = _store(tokens)

    assert await store.is_available() is True
    assert tokens.cleared is False


async def test_is_available_false_and_clears_when_refresh_fails():
    """The exact regression: a present-but-dead token must report False."""
    tokens = _FakeTokens(loaded=True, fresh=False)
    store = _store(tokens)

    assert await store.is_available() is False
    assert tokens.cleared is True


# ── background token-refresh poller ──────────────────────────────────

async def test_token_poll_loop_refreshes_when_signed_in(monkeypatch):
    tokens = _FakeTokens(loaded=True, fresh=True)
    store = _store(tokens)
    monkeypatch.setattr(store, "TOKEN_POLL_INTERVAL_SECONDS", 0.01)

    store.start_token_refresh_polling()
    await asyncio.sleep(0.05)
    await store.stop_token_refresh_polling()

    assert tokens.load_calls >= 1
    assert tokens.refresh_calls >= 1
    assert tokens.cleared is False


async def test_token_poll_loop_clears_dead_session(monkeypatch):
    tokens = _FakeTokens(loaded=True, fresh=False)
    store = _store(tokens)
    monkeypatch.setattr(store, "TOKEN_POLL_INTERVAL_SECONDS", 0.01)

    store.start_token_refresh_polling()
    await asyncio.sleep(0.05)
    await store.stop_token_refresh_polling()

    assert tokens.cleared is True


async def test_token_poll_loop_skips_refresh_when_not_signed_in(monkeypatch):
    tokens = _FakeTokens(loaded=False)
    store = _store(tokens)
    monkeypatch.setattr(store, "TOKEN_POLL_INTERVAL_SECONDS", 0.01)

    store.start_token_refresh_polling()
    await asyncio.sleep(0.05)
    await store.stop_token_refresh_polling()

    assert tokens.load_calls >= 1
    assert tokens.refresh_calls == 0


async def test_start_token_refresh_polling_is_idempotent():
    store = _store(_FakeTokens())

    store.start_token_refresh_polling()
    first_task = store._poll_task
    store.start_token_refresh_polling()

    assert store._poll_task is first_task
    await store.stop_token_refresh_polling()


async def test_stop_token_refresh_polling_without_start_is_a_noop():
    store = _store(_FakeTokens())

    await store.stop_token_refresh_polling()  # must not raise

    assert store._poll_task is None
