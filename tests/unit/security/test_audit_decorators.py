"""Deep executable tests — security/audit_decorators.py.

Source : py_modules/unifideck/security/audit_decorators.py
Fiche  : OP   Critical (security) — coverage floor 95%.

Audit decorators that wrap auth flows / token ops and emit
SECURITY_* events on the host's _bus. Every success /
failure / migration / no-bus branch is exercised.
"""
from __future__ import annotations

from typing import Any

import pytest

from unifideck.core.types import Events
from unifideck.security.audit_decorators import (
    _emit_audit,
    _maybe_emit_migration,
    audit_auth_flow,
    audit_token_op,
)


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[Any, dict]] = []

    async def emit(self, event: Any, **kw: Any) -> None:
        # Source restructured: _emit_audit / _maybe_emit_migration
        # are now `async def` and `await bus.emit(...)`.
        self.events.append((event, kw))


def test_module_imports() -> None:
    import unifideck.security.audit_decorators as mod
    assert mod.audit_auth_flow is audit_auth_flow


# ========================================================= #
# audit_auth_flow
# ========================================================= #
@pytest.mark.asyncio
async def test_audit_auth_flow_success() -> None:
    class _Host:
        def __init__(self) -> None:
            self._bus = _Bus()

        @audit_auth_flow(store="epic",
                         method="oauth_cli")
        async def do_auth(self) -> str:
            return "ok"

    h = _Host()
    out = await h.do_auth()
    assert out == "ok"
    names = [e[0] for e in h._bus.events]
    assert Events.SECURITY_AUTH_FLOW_STARTED in names
    assert Events.SECURITY_AUTH_FLOW_COMPLETED in names


@pytest.mark.asyncio
async def test_audit_auth_flow_failure() -> None:
    class _Host:
        def __init__(self) -> None:
            self._bus = _Bus()

        @audit_auth_flow(store="gog")
        async def do_auth(self) -> str:
            raise RuntimeError("auth boom")

    h = _Host()
    with pytest.raises(RuntimeError, match="auth boom"):
        await h.do_auth()
    names = [e[0] for e in h._bus.events]
    assert Events.SECURITY_AUTH_FLOW_STARTED in names
    assert Events.SECURITY_AUTH_FLOW_FAILED in names
    # failure event carries the exception type
    failed = next(
        e for e in h._bus.events
        if e[0] == Events.SECURITY_AUTH_FLOW_FAILED)
    assert failed[1]["reason"] == "RuntimeError"


@pytest.mark.asyncio
async def test_audit_auth_flow_no_bus() -> None:
    class _Host:
        _bus = None

        @audit_auth_flow(store="amazon")
        async def do_auth(self) -> str:
            return "done"

    h = _Host()
    out = await h.do_auth()  # no bus -> no emit, no raise
    assert out == "done"


# ========================================================= #
# audit_token_op
# ========================================================= #
@pytest.mark.asyncio
async def test_audit_token_op_passthrough() -> None:
    class _Host:
        def __init__(self) -> None:
            self._bus = _Bus()

        @audit_token_op(operation="load",
                        store="gog")
        async def load_tokens(self) -> str:
            return "tokens"

    h = _Host()
    assert await h.load_tokens() == "tokens"
    # non-migrate op -> no migration event
    assert h._bus.events == []


@pytest.mark.asyncio
async def test_audit_token_op_migrate_emits() -> None:
    class _Host:
        def __init__(self) -> None:
            self._bus = _Bus()
            self._migration_occurred = True

        @audit_token_op(operation="migrate",
                        store="gog")
        async def migrate(self) -> str:
            return "/new/token/path"

    h = _Host()
    out = await h.migrate()
    assert out == "/new/token/path"
    names = [e[0] for e in h._bus.events]
    assert Events.SECURITY_TOKEN_FILE_MIGRATED in names
    # flag reset
    assert h._migration_occurred is False


@pytest.mark.asyncio
async def test_audit_token_op_migrate_no_flag() -> None:
    class _Host:
        def __init__(self) -> None:
            self._bus = _Bus()
            self._migration_occurred = False

        @audit_token_op(operation="migrate",
                        store="gog")
        async def migrate(self) -> str:
            return "/path"

    h = _Host()
    await h.migrate()
    # flag was False -> no migration event
    assert h._bus.events == []


@pytest.mark.asyncio
async def test_audit_token_op_migrate_non_str() -> None:
    class _Host:
        def __init__(self) -> None:
            self._bus = _Bus()
            self._migration_occurred = True

        @audit_token_op(operation="migrate",
                        store="gog")
        async def migrate(self) -> bool:
            return True  # not a str -> no migration emit

    h = _Host()
    await h.migrate()
    assert h._bus.events == []


# ========================================================= #
# _emit_audit
# ========================================================= #
@pytest.mark.asyncio
async def test_emit_audit_no_bus() -> None:
    await _emit_audit(None, "SECURITY_AUTH_FLOW_STARTED")
    # no raise


@pytest.mark.asyncio
async def test_emit_audit_ok() -> None:
    bus = _Bus()
    await _emit_audit(
        bus, "SECURITY_AUTH_FLOW_STARTED",
        store="epic")
    assert bus.events
    assert bus.events[0][0] == \
        Events.SECURITY_AUTH_FLOW_STARTED


@pytest.mark.asyncio
async def test_emit_audit_unknown_event() -> None:
    bus = _Bus()
    # getattr(Events, "NOPE") raises -> caught, swallowed
    await _emit_audit(bus, "NOPE_NOT_AN_EVENT")
    assert bus.events == []


@pytest.mark.asyncio
async def test_emit_audit_bus_emit_raises() -> None:
    class _BadBus:
        def emit(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("bus down")

    # exception swallowed (logged at debug)
    await _emit_audit(
        _BadBus(), "SECURITY_AUTH_FLOW_STARTED")


# ========================================================= #
# _maybe_emit_migration
# ========================================================= #
@pytest.mark.asyncio
async def test_maybe_emit_migration_no_bus() -> None:
    await _maybe_emit_migration(
        None, object(), "gog", "/p")  # no raise


@pytest.mark.asyncio
async def test_maybe_emit_migration_no_flag() -> None:
    class _Inst:
        _migration_occurred = False

    bus = _Bus()
    await _maybe_emit_migration(bus, _Inst(), "gog", "/p")
    assert bus.events == []


@pytest.mark.asyncio
async def test_maybe_emit_migration_ok() -> None:
    class _Inst:
        _migration_occurred = True

    bus = _Bus()
    inst = _Inst()
    await _maybe_emit_migration(bus, inst, "gog", "/new")
    assert bus.events
    assert bus.events[0][0] == \
        Events.SECURITY_TOKEN_FILE_MIGRATED
    assert inst._migration_occurred is False


@pytest.mark.asyncio
async def test_maybe_emit_migration_readonly_flag() -> None:
    """An instance where the flag can't be reset (e.g.
    __slots__/property) -> AttributeError swallowed."""
    class _Inst:
        __slots__ = ()
        _migration_occurred = True  # class attr, not settable

    bus = _Bus()
    # should still emit and not raise even though resetting
    # the instance attribute fails
    await _maybe_emit_migration(
        bus, _Inst(), "gog", "/new")
    assert bus.events
