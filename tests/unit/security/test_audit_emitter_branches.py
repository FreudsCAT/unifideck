"""Deep branch tests — security/audit_emitter.py.

Source : py_modules/unifideck/security/audit_emitter.py
Fiche  : OP   Critical (security) — coverage floor 95%.

Targets the residual branches: _safe_emit (no bus / running
loop / no loop / emit raises), emit_token_age_exceeded,
the audit_auth_flow decorator (no bus, success, exception),
and _extract_failure_reason attribute fallback chain.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import unifideck.security.audit_emitter as AE
from unifideck.security.audit_emitter import (
    audit_auth_flow,
    emit_token_age_exceeded,
    _extract_failure_reason,
    _safe_emit,
)


class _Bus:
    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, ev: Any, **kw: Any
                   ) -> None:
        self.events.append((ev, kw))


def test_module_imports() -> None:
    assert hasattr(AE, "_safe_emit")


# ========================================================= #
# _safe_emit
# ========================================================= #
def test_safe_emit_no_bus() -> None:
    _safe_emit(None, "SECURITY_TOKEN_DECRYPTED")  # no-op


@pytest.mark.asyncio
async def test_safe_emit_running_loop() -> None:
    bus = _Bus()
    _safe_emit(bus, "SECURITY_TOKEN_DECRYPTED",
               byte_count=5)
    # task scheduled on the running loop
    await asyncio.sleep(0.02)
    assert bus.events
    assert bus.events[0][1]["byte_count"] == 5


def test_safe_emit_no_running_loop() -> None:
    bus = _Bus()
    # sync context: no running loop -> event dropped,
    # no exception
    _safe_emit(bus, "SECURITY_TOKEN_DECRYPTED")
    assert bus.events == []


@pytest.mark.asyncio
async def test_safe_emit_unknown_event() -> None:
    bus = _Bus()
    # getattr(Events, "NOPE") raises -> swallowed
    _safe_emit(bus, "NOT_A_REAL_EVENT")
    await asyncio.sleep(0.01)
    assert bus.events == []


@pytest.mark.asyncio
async def test_safe_emit_redacts_secrets() -> None:
    bus = _Bus()
    _safe_emit(bus, "SECURITY_TOKEN_DECRYPTED",
               access_token="SUPERSECRET")
    await asyncio.sleep(0.02)
    assert bus.events
    # redacted before reaching the bus
    assert bus.events[0][1]["access_token"] == \
        "<redacted>"


# ========================================================= #
# emit_token_age_exceeded
# ========================================================= #
@pytest.mark.asyncio
async def test_emit_token_age_exceeded() -> None:
    bus = _Bus()
    emit_token_age_exceeded(
        bus, "gog", age_seconds=123.456,
        max_age_seconds=100.0)
    await asyncio.sleep(0.02)
    assert bus.events
    ev, kw = bus.events[0]
    assert kw["store"] == "gog"
    assert kw["age_seconds"] == 123.5


# ========================================================= #
# _extract_failure_reason
# ========================================================= #
def test_extract_reason_error() -> None:
    class _R:
        error = "boom_error"

    assert _extract_failure_reason(_R()) == \
        "boom_error"


def test_extract_reason_error_code() -> None:
    class _R:
        error = None
        error_code = "E42"

    assert _extract_failure_reason(_R()) == "E42"


def test_extract_reason_message() -> None:
    class _R:
        message = "human readable"

    assert _extract_failure_reason(_R()) == \
        "human readable"


def test_extract_reason_unknown() -> None:
    class _R:
        pass

    assert _extract_failure_reason(_R()) == \
        "unknown"


def test_extract_reason_capped() -> None:
    class _R:
        error = "x" * 200

    out = _extract_failure_reason(_R())
    assert len(out) == 64


# ========================================================= #
# audit_auth_flow decorator
# ========================================================= #
@pytest.mark.asyncio
async def test_decorator_no_bus() -> None:
    class _Host:
        _bus = None

        @audit_auth_flow(store="gog")
        async def go(self) -> str:
            return "ok"

    out = await _Host().go()
    assert out == "ok"


@pytest.mark.asyncio
async def test_decorator_success() -> None:
    bus = _Bus()

    class _Result:
        success = True

    class _Host:
        def __init__(self) -> None:
            self._bus = bus

        @audit_auth_flow(store="gog",
                         method="oauth_browser")
        async def go(self) -> _Result:
            return _Result()

    res = await _Host().go()
    assert res.success is True
    await asyncio.sleep(0.02)
    # started + completed emitted
    assert len(bus.events) >= 2


@pytest.mark.asyncio
async def test_decorator_failure_result() -> None:
    bus = _Bus()

    class _Result:
        success = False
        error = "token_exchange_failed"

    class _Host:
        def __init__(self) -> None:
            self._bus = bus

        @audit_auth_flow(store="gog")
        async def go(self) -> _Result:
            return _Result()

    res = await _Host().go()
    assert res.success is False
    await asyncio.sleep(0.02)
    assert len(bus.events) >= 2


@pytest.mark.asyncio
async def test_decorator_raises() -> None:
    bus = _Bus()

    class _Host:
        def __init__(self) -> None:
            self._bus = bus

        @audit_auth_flow(store="gog")
        async def go(self) -> None:
            raise RuntimeError("flow blew up")

    with pytest.raises(RuntimeError,
                       match="flow blew up"):
        await _Host().go()
    await asyncio.sleep(0.02)
    # started + failed(exception) emitted
    assert len(bus.events) >= 2
