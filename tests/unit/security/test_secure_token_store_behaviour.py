"""Deep behavioural tests — security/secure_token_store.py.

Source : py_modules/unifideck/security/secure_token_store.py
Fiche  : OP   Critical (security) — coverage floor 95%.

Complements the shallow signature tests with a real
encrypt→decrypt round-trip through AES-GCM (Scrypt KDF over
a fake machine-id), plus every documented failure branch:
short blob, bad magic, GCM tamper, non-JSON / non-dict
plaintext, missing machine-id, and the bus audit emitter.
"""
from __future__ import annotations

from typing import Any

import pytest

import unifideck.security.secure_token_store as STS
from unifideck.security.secure_token_store import (
    SecureTokenStore,
    SecureTokenStoreError,
    _MAGIC,
)
from unifideck.security.device_identity import (
    DeviceIdentityError,
)


class _FakeIdentity:
    def __init__(self, mid: str | bytes = "fake-machine-id-1234",
                 raises: bool = False) -> None:
        self._mid = mid
        self._raises = raises

    def read(self) -> Any:
        if self._raises:
            raise DeviceIdentityError("no machine-id")
        return self._mid


class _Bus:
    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, event: Any, **kw: Any) -> None:
        # Source schedules `await self._bus.emit(...)` on the
        # running loop via create_task, so emit must be async.
        self.events.append((event, kw))


def _store(**kw: Any) -> SecureTokenStore:
    return SecureTokenStore(
        device_identity=_FakeIdentity(**kw))


def test_module_imports() -> None:
    assert STS.SecureTokenStore is SecureTokenStore


# ========================================================= #
# round-trip
# ========================================================= #
def test_encrypt_decrypt_roundtrip() -> None:
    s = _store()
    payload = {"access_token": "AT",
               "refresh_token": "RT"}
    blob = s.encrypt_payload(payload)
    assert blob.startswith(_MAGIC)
    out = s.decrypt_payload(blob)
    assert out["access_token"] == "AT"
    assert out["refresh_token"] == "RT"


def test_encrypt_adds_timestamp() -> None:
    s = _store()
    blob = s.encrypt_payload({"k": "v"})
    out = s.decrypt_payload(blob)
    age = SecureTokenStore.payload_age_seconds(out)
    assert age is not None
    assert age >= 0.0


# ========================================================= #
# payload_age_seconds
# ========================================================= #
def test_age_missing_timestamp() -> None:
    assert SecureTokenStore.payload_age_seconds(
        {"no": "ts"}) is None


def test_age_non_numeric() -> None:
    assert SecureTokenStore.payload_age_seconds(
        {"_unifideck_encrypted_at": "soon"}) is None


def test_age_clamped_non_negative() -> None:
    # timestamp far in the future -> clamp to 0.0
    out = SecureTokenStore.payload_age_seconds(
        {"_unifideck_encrypted_at": 9e18},
        now=0.0)
    assert out == 0.0


def test_age_positive() -> None:
    out = SecureTokenStore.payload_age_seconds(
        {"_unifideck_encrypted_at": 100.0},
        now=160.0)
    assert out == 60.0


# ========================================================= #
# is_encrypted
# ========================================================= #
def test_is_encrypted_true() -> None:
    s = _store()
    blob = s.encrypt_payload({"a": 1})
    assert s.is_encrypted(blob) is True


def test_is_encrypted_false() -> None:
    s = _store()
    assert s.is_encrypted(b"plaintext") is False


# ========================================================= #
# decrypt_payload — failure branches
# ========================================================= #
def test_decrypt_too_short() -> None:
    s = _store()
    with pytest.raises(SecureTokenStoreError,
                       match="too short"):
        s.decrypt_payload(b"UFD1tiny")


def test_decrypt_bad_magic() -> None:
    s = _store()
    blob = b"XXXX" + b"\x00" * 40
    with pytest.raises(SecureTokenStoreError,
                       match="magic"):
        s.decrypt_payload(blob)


def test_decrypt_gcm_tampered() -> None:
    s = _store()
    blob = bytearray(s.encrypt_payload({"a": 1}))
    blob[-1] ^= 0xFF  # flip a ciphertext byte
    with pytest.raises(SecureTokenStoreError,
                       match="authentication failed"):
        s.decrypt_payload(bytes(blob))


def test_decrypt_not_json(monkeypatch) -> None:
    s = _store()

    # _decrypt returns non-JSON plaintext
    monkeypatch.setattr(
        s, "_decrypt",
        lambda blob: b"\xff\xfenot json")
    with pytest.raises(SecureTokenStoreError,
                       match="not valid JSON"):
        s.decrypt_payload(b"whatever")


def test_decrypt_not_dict(monkeypatch) -> None:
    s = _store()
    monkeypatch.setattr(
        s, "_decrypt",
        lambda blob: b"[1, 2, 3]")
    with pytest.raises(SecureTokenStoreError,
                       match="not a JSON object"):
        s.decrypt_payload(b"whatever")


# ========================================================= #
# _get_key — missing machine-id
# ========================================================= #
def test_get_key_no_machine_id() -> None:
    s = SecureTokenStore(
        device_identity=_FakeIdentity(raises=True))
    with pytest.raises(SecureTokenStoreError,
                       match="without machine-id"):
        s.encrypt_payload({"a": 1})


def test_get_key_cached() -> None:
    s = _store()
    s.encrypt_payload({"a": 1})  # derives + caches
    cached = s._key
    assert cached is not None
    # second call reuses the cached key
    s.encrypt_payload({"b": 2})
    assert s._key is cached


# ========================================================= #
# _emit_security_event (bus audit)
# ========================================================= #
def test_emit_event_no_bus() -> None:
    s = _store()
    s._emit_security_event(
        "SECURITY_TOKEN_DECRYPTED")  # no bus, no-op


@pytest.mark.asyncio
async def test_emit_event_with_bus() -> None:
    import asyncio

    bus = _Bus()
    s = SecureTokenStore(
        device_identity=_FakeIdentity(), bus=bus)
    # _emit_security_event schedules bus.emit via
    # loop.create_task (fire-and-forget) and DROPS the
    # emission entirely when no event loop is running. So
    # this must run inside an event loop, then yield once
    # so the scheduled task can deliver before asserting.
    s.encrypt_payload({"a": 1})
    await asyncio.sleep(0.02)
    # at least one SECURITY_* event emitted
    assert len(bus.events) >= 1


def test_emit_event_bus_raises_swallowed() -> None:
    class _BadBus:
        def emit(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("bus down")

    s = SecureTokenStore(
        device_identity=_FakeIdentity(),
        bus=_BadBus())
    # crypto must succeed even if audit emit raises
    blob = s.encrypt_payload({"a": 1})
    assert blob.startswith(_MAGIC)


def test_emit_event_unknown_name_swallowed() -> None:
    bus = _Bus()
    s = SecureTokenStore(
        device_identity=_FakeIdentity(), bus=bus)
    # nonexistent event name -> getattr raises ->
    # swallowed at debug level
    s._emit_security_event("NOT_A_REAL_EVENT")
