"""Unit tests for core.sync_availability.refresh_store_availability.

Regression: ``registry.available()`` (consumed by ``_setup_sync``
right after this runs) only reflects each store's ``_cached_available``
flag, and nothing refreshed that flag on a successful login -- only an
explicit status check (opening the Settings/Store tab, or a fresh
boot) did. A sync run immediately after signing into a store silently
skipped it: the newly-authed store only "became available" after the
next restart forced a fresh check. ``refresh_store_availability`` now
runs a fresh ``is_available()`` per store before every sync.
"""
from __future__ import annotations

from unifideck.core.sync_availability import refresh_store_availability


class _FakeStore:
    def __init__(
        self, name: str, *, available: bool, raises: bool = False,
    ) -> None:
        self.store_name = name
        self._cached_available = not available  # deliberately stale/wrong
        self._available = available
        self._raises = raises
        self.calls = 0

    async def is_available(self) -> bool:
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._available


class _FakeRegistry:
    def __init__(self, stores: list[_FakeStore]) -> None:
        self._stores = stores

    def all(self) -> list[_FakeStore]:
        return self._stores


async def test_refreshes_every_store():
    microsoft = _FakeStore("microsoft", available=True)
    epic = _FakeStore("epic", available=False)

    await refresh_store_availability(_FakeRegistry([microsoft, epic]))

    assert microsoft._cached_available is True
    assert epic._cached_available is False
    assert microsoft.calls == 1
    assert epic.calls == 1


async def test_the_regression_fresh_login_flips_stale_cache():
    """The exact bug: a store cached False before login now reflects
    the real post-login state without needing a restart."""
    store = _FakeStore("microsoft", available=True)
    store._cached_available = False  # stale, from before login

    await refresh_store_availability(_FakeRegistry([store]))

    assert store._cached_available is True


async def test_one_broken_check_does_not_block_the_others():
    broken = _FakeStore("gog", available=True, raises=True)
    healthy = _FakeStore("epic", available=True)

    await refresh_store_availability(_FakeRegistry([broken, healthy]))

    assert healthy._cached_available is True
    assert broken.calls == 1  # attempted, but its failure didn't propagate
