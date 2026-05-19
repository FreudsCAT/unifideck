"""Deep behavioural tests — security/device_identity.py.

Source : py_modules/unifideck/security/device_identity.py
Fiche  : OP   Critical (security) — coverage floor 95%.

Machine-id reader: 32-hex-char validation, OS error
mapping, result caching, and the FakeDeviceIdentity test
double's own validation. Real temp machine-id files.
"""
from __future__ import annotations

import pytest

import unifideck.security.device_identity as DI
from unifideck.security.device_identity import (
    DeviceIdentity,
    DeviceIdentityError,
    FakeDeviceIdentity,
)

_VALID = "0123456789abcdef0123456789abcdef"


def test_module_imports() -> None:
    assert DI.DeviceIdentity is DeviceIdentity


# ========================================================= #
# _looks_valid
# ========================================================= #
def test_looks_valid_ok() -> None:
    assert DeviceIdentity._looks_valid(_VALID) \
        is True


def test_looks_valid_wrong_length() -> None:
    assert DeviceIdentity._looks_valid(
        "abc") is False


def test_looks_valid_non_hex() -> None:
    assert DeviceIdentity._looks_valid(
        "z" * 32) is False


# ========================================================= #
# DeviceIdentity.read
# ========================================================= #
def test_read_ok(tmp_path) -> None:
    f = tmp_path / "machine-id"
    f.write_text(_VALID + "\n")
    d = DeviceIdentity(str(f))
    assert d.read() == _VALID


def test_read_uppercase_normalised(
    tmp_path,
) -> None:
    f = tmp_path / "machine-id"
    f.write_text("ABCDEF0123456789ABCDEF0123456789")
    d = DeviceIdentity(str(f))
    assert d.read() == \
        "abcdef0123456789abcdef0123456789"


def test_read_missing_file(tmp_path) -> None:
    d = DeviceIdentity(str(tmp_path / "nope"))
    with pytest.raises(DeviceIdentityError,
                       match="cannot read"):
        d.read()


def test_read_malformed(tmp_path) -> None:
    f = tmp_path / "machine-id"
    f.write_text("not-a-machine-id")
    d = DeviceIdentity(str(f))
    with pytest.raises(DeviceIdentityError,
                       match="malformed"):
        d.read()


def test_read_caches(tmp_path) -> None:
    f = tmp_path / "machine-id"
    f.write_text(_VALID)
    d = DeviceIdentity(str(f))
    first = d.read()
    # mutate the file; cached value must persist
    f.write_text("ffffffffffffffffffffffffffffffff")
    assert d.read() == first


# ========================================================= #
# FakeDeviceIdentity
# ========================================================= #
def test_fake_identity_ok() -> None:
    fake = FakeDeviceIdentity(_VALID)
    assert fake.read() == _VALID


def test_fake_identity_rejects_invalid() -> None:
    with pytest.raises(ValueError,
                       match="got"):
        FakeDeviceIdentity("too-short")
