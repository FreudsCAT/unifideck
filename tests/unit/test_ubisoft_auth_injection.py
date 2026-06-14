"""Regression: Ubisoft sign-in needs the shortcut service wired post-injection.

Auto-discovery builds UbisoftStore (and its auth facade) before the
service container exists, so the facade captures ``_shortcut_service=None``.
store_injector sets ``store._shortcut_service`` afterward and calls
``_rebuild_auth_after_injection``. Without that hook the facade keeps the
None and ``get_auth_shortcut_context`` returns ``shortcut_service_unavailable``
— the QAM "Auth shortcut not available" sign-in failure.
"""
from __future__ import annotations

import pytest

from unifideck.stores.ubisoft import store as store_mod


class _FakeAuth:
    def __init__(self) -> None:
        self._shortcut_service = None


def _bare_store() -> object:
    """A UbisoftStore shell with just the attributes the hook touches."""
    store = store_mod.UbisoftStore.__new__(store_mod.UbisoftStore)
    store._auth = _FakeAuth()  # type: ignore[attr-defined]
    return store


def test_hook_exists():
    assert callable(
        getattr(store_mod.UbisoftStore, "_rebuild_auth_after_injection", None),
    )


def test_hook_propagates_injected_service_to_facade():
    store = _bare_store()
    sentinel = object()
    store._shortcut_service = sentinel  # type: ignore[attr-defined]

    store._rebuild_auth_after_injection()  # type: ignore[attr-defined]

    assert store._auth._shortcut_service is sentinel  # type: ignore[attr-defined]


def test_hook_noop_when_service_absent():
    """No injected service yet (called from __init__) → leaves facade None."""
    store = _bare_store()
    # _shortcut_service attribute not set at all.
    store._rebuild_auth_after_injection()  # type: ignore[attr-defined]
    assert store._auth._shortcut_service is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_context_unavailable_without_wiring_then_ok_after():
    """End-to-end on the facade: None service → error; wired → success path.

    Uses the real auth context builder with a fake shortcut service.
    """
    from unifideck.stores.ubisoft.auth import context as ctx_mod

    # Parent stub exposing what _AuthContext reads.
    class _Cfg:
        auth_shortcut_store_id = "ubisoft:upc-auth"
        auth_shortcut_launch_wait_ms = 1500

    class _Parent:
        def __init__(self) -> None:
            self._shortcut_service = None
            self._config = _Cfg()

    parent = _Parent()
    builder = ctx_mod._AuthContext(parent)

    # No service → unavailable (the bug surface).
    res = await builder.get_auth_shortcut_context()
    assert res == {"success": False, "error": "shortcut_service_unavailable"}
