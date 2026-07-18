"""Unit tests for bootstrap.boot._start_store_background_tasks.

Covers the boot-time wiring for MicrosoftStore's background
token-refresh poller: started when the store is registered and
exposes the hook, silently skipped when absent, and never allowed to
block boot on failure.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from unifideck.bootstrap.boot import _start_store_background_tasks


def _plugin_with_store(store: object | None) -> SimpleNamespace:
    registry = SimpleNamespace(get=lambda name: store if name == "microsoft" else None)
    return SimpleNamespace(registry=registry)


async def test_starts_polling_when_store_present():
    starter = MagicMock()
    store = SimpleNamespace(start_token_refresh_polling=starter)

    await _start_store_background_tasks(_plugin_with_store(store))

    starter.assert_called_once_with()


async def test_noop_when_microsoft_not_registered():
    await _start_store_background_tasks(_plugin_with_store(None))  # must not raise


async def test_noop_when_store_lacks_the_hook():
    store = SimpleNamespace()  # no start_token_refresh_polling attribute

    await _start_store_background_tasks(_plugin_with_store(store))  # must not raise


async def test_swallows_exception_from_starter():
    store = SimpleNamespace(
        start_token_refresh_polling=MagicMock(side_effect=RuntimeError("boom")),
    )

    await _start_store_background_tasks(_plugin_with_store(store))  # must not raise
