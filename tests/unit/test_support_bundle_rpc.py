"""Tests for the ``capture_logs`` RPC and its service facade.

Instantiates the mixin directly against a minimal host, matching the
other RPC tests. Because ``@auto_wrap_rpc_methods`` is applied to the
``Plugin`` class rather than to the mixins, the coroutine under test is
unwrapped here — so these assert on the raw return value, not on the
``{success, error, data}`` envelope the frontend sees.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from unifideck.rpc.errors import RpcError
from unifideck.rpc.mixins.observability import ObservabilityRPCMixin
from unifideck.services.support_bundle import SupportBundleService


class _FakeBundleService:
    """Records how it was called and returns a canned payload."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or {"archive_path": "/home/u/Downloads/b.zip"}
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def capture(
        self, dest_path: str = "", extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((dest_path, extra))
        return self.result


class _RaisingService:
    """Raises whatever it was constructed with."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def capture(
        self, dest_path: str = "", extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise self.error


class _Host(ObservabilityRPCMixin):
    """Minimal stand-in for the composed Plugin class."""

    def __init__(self, services: Any) -> None:
        self.services = services
        self.bus = SimpleNamespace()
        self.runtime_probes = None


def _host(service: Any, **extra: Any) -> _Host:
    """Build a host whose container exposes ``support_bundle``."""
    return _Host(SimpleNamespace(support_bundle=service, **extra))


# ── happy path ────────────────────────────────────────────────────
async def test_returns_the_service_payload_unwrapped() -> None:
    service = _FakeBundleService({"archive_path": "/x/b.zip", "bytes": 42})
    result = await _host(service).capture_logs()
    assert result == {"archive_path": "/x/b.zip", "bytes": 42}


async def test_destination_override_is_forwarded() -> None:
    service = _FakeBundleService()
    await _host(service).capture_logs("/run/media/deck/SD")
    assert service.calls[0][0] == "/run/media/deck/SD"


# ── error mapping ─────────────────────────────────────────────────
async def test_missing_service_raises_service_unavailable() -> None:
    host = _Host(SimpleNamespace())
    with pytest.raises(RpcError) as caught:
        await host.capture_logs()
    assert caught.value.code == "service_unavailable"
    assert caught.value.context["service"] == "support_bundle"


async def test_unwritable_destination_gets_its_own_code() -> None:
    """The one failure a user can actually act on.

    It has to be distinguishable from a generic crash so the UI can say
    something more useful than "capture failed".
    """
    service = _RaisingService(OSError("no writable destination; tried [...]"))
    with pytest.raises(RpcError) as caught:
        await _host(service).capture_logs()
    assert caught.value.code == "bundle_dest_unwritable"
    assert "writable" in caught.value.context["detail"]


async def test_unexpected_failure_becomes_bundle_failed() -> None:
    service = _RaisingService(ValueError("something odd"))
    with pytest.raises(RpcError) as caught:
        await _host(service).capture_logs()
    assert caught.value.code == "bundle_failed"
    assert "something odd" in caught.value.context["detail"]


async def test_typed_errors_pass_through_unchanged() -> None:
    """A typed error must not be swallowed into the generic one."""
    service = _RaisingService(RpcError("already_typed", detail="keep me"))
    with pytest.raises(RpcError) as caught:
        await _host(service).capture_logs()
    assert caught.value.code == "already_typed"


# ── the extra payload ─────────────────────────────────────────────
async def test_flags_and_probes_are_folded_in() -> None:
    """Facts only this layer can see reach the environment report."""
    service = _FakeBundleService()
    flags = SimpleNamespace(get_flags=lambda: {"beta": True})
    host = _host(service, feature_flags=flags)
    host.runtime_probes = [{"name": "cef", "severity": "info"}]
    await host.capture_logs()
    extra = service.calls[0][1] or {}
    assert extra["feature_flags"] == {"beta": True}
    assert extra["runtime_probes"][0]["name"] == "cef"


async def test_extra_is_still_a_dict_with_neither_available() -> None:
    service = _FakeBundleService()
    await _host(service).capture_logs()
    assert service.calls[0][1] == {}


async def test_a_broken_flag_service_does_not_cost_the_bundle() -> None:
    """Optional context must never be able to fail the capture."""
    def _explode() -> dict[str, Any]:
        raise RuntimeError("flag service down")

    service = _FakeBundleService()
    host = _host(service, feature_flags=SimpleNamespace(get_flags=_explode))
    result = await host.capture_logs()
    assert result["archive_path"]
    assert service.calls[0][1] == {}


# ── service facade ────────────────────────────────────────────────
async def test_concurrent_capture_is_refused_not_duplicated() -> None:
    """Two taps must not build two archives or race on the filename."""
    service = SupportBundleService(config=None, paths=None)
    await service._lock.acquire()
    try:
        result = await service.capture()
    finally:
        service._lock.release()
    assert result["in_progress"] is True
    assert result["archive_path"] is None


async def test_capture_runs_the_collector_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocking file I/O must be thread-hopped, not inline."""
    seen: dict[str, Any] = {}

    def _fake_capture(
        dest_path: str, config: Any, paths: Any, extra: Any,
    ) -> dict[str, Any]:
        seen["dest_path"] = dest_path
        seen["extra"] = extra
        return {"archive_path": "/x/b.zip"}

    monkeypatch.setattr(
        "unifideck.services.support_bundle.collect.capture_bundle", _fake_capture,
    )
    service = SupportBundleService(config=None, paths=None)
    result = await service.capture("/tmp/dest", extra={"a": 1})
    assert result["archive_path"] == "/x/b.zip"
    assert seen["dest_path"] == "/tmp/dest"
    assert seen["extra"] == {"a": 1}


async def test_lock_is_released_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed capture must not wedge the button forever."""
    def _explode(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise OSError("nowhere writable")

    monkeypatch.setattr(
        "unifideck.services.support_bundle.collect.capture_bundle", _explode,
    )
    service = SupportBundleService(config=None, paths=None)
    with pytest.raises(OSError, match="nowhere writable"):
        await service.capture()
    assert not service._lock.locked()
