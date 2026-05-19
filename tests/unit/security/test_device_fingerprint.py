"""Deep executable tests — security/device_fingerprint.py.

Source : py_modules/unifideck/security/device_fingerprint.py
Fiche  : OP   Critical (security) — coverage floor 95%.

DeviceFingerprint: hash the machine id, persist it, detect
mismatches on later boots. Real temp fingerprint files; a
fake DeviceIdentity supplies a controllable machine id.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from unifideck.security.device_fingerprint import (
    DeviceFingerprint,
    FingerprintState,
)


class _Identity:
    def __init__(self, mid: str = "machine-abc"
                 ) -> None:
        self._mid = mid

    def read(self) -> str:
        return self._mid


def _fp(tmp_path, mid: str = "machine-abc"
        ) -> DeviceFingerprint:
    return DeviceFingerprint(
        str(tmp_path / "fp.json"),
        device_identity=_Identity(mid))  # type: ignore[arg-type]


def test_module_imports() -> None:
    import unifideck.security.device_fingerprint as mod
    assert mod.DeviceFingerprint is DeviceFingerprint


# ========================================================= #
# verify_or_initialize
# ========================================================= #
def test_verify_first_time_initializes(
    tmp_path,
) -> None:
    fp = _fp(tmp_path)
    state = fp.verify_or_initialize()
    assert state.is_new is True
    assert state.mismatch is False
    assert state.machine_id_hash.startswith("sha256:")
    assert Path(fp._path).is_file()


def test_verify_existing_match(tmp_path) -> None:
    fp = _fp(tmp_path)
    fp.verify_or_initialize()  # init
    state = fp.verify_or_initialize()  # second boot
    assert state.is_new is False
    assert state.mismatch is False


def test_verify_mismatch(tmp_path) -> None:
    fp1 = _fp(tmp_path, mid="machine-old")
    fp1.verify_or_initialize()
    # same file, different machine id
    fp2 = _fp(tmp_path, mid="machine-new")
    fp2._path = fp1._path
    state = fp2.verify_or_initialize()
    assert state.mismatch is True
    assert state.is_new is False


def test_verify_preserves_first_seen(
    tmp_path,
) -> None:
    fp = _fp(tmp_path)
    s1 = fp.verify_or_initialize()
    s2 = fp.verify_or_initialize()
    assert s2.first_seen == s1.first_seen


# ========================================================= #
# reinitialize
# ========================================================= #
def test_reinitialize(tmp_path) -> None:
    fp = _fp(tmp_path)
    fp.verify_or_initialize()
    state = fp.reinitialize()
    assert state.is_new is True
    assert state.mismatch is False


# ========================================================= #
# _compute_current_hash
# ========================================================= #
def test_compute_hash_deterministic(tmp_path) -> None:
    fp = _fp(tmp_path, mid="stable-id")
    h1 = fp._compute_current_hash()
    h2 = fp._compute_current_hash()
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_compute_hash_varies_by_id(tmp_path) -> None:
    a = _fp(tmp_path, mid="id-a")._compute_current_hash()
    b = _fp(tmp_path, mid="id-b")._compute_current_hash()
    assert a != b


# ========================================================= #
# _load
# ========================================================= #
def test_load_missing(tmp_path) -> None:
    fp = _fp(tmp_path)
    assert fp._load() is None


def test_load_corrupt(tmp_path) -> None:
    fp = _fp(tmp_path)
    Path(fp._path).write_text("{ not json")
    assert fp._load() is None


def test_load_not_dict(tmp_path) -> None:
    fp = _fp(tmp_path)
    Path(fp._path).write_text(json.dumps([1, 2, 3]))
    assert fp._load() is None


def test_load_ok(tmp_path) -> None:
    fp = _fp(tmp_path)
    Path(fp._path).write_text(
        json.dumps({"machine_id_hash": "sha256:x"}))
    out = fp._load()
    assert out == {"machine_id_hash": "sha256:x"}


# ========================================================= #
# _save
# ========================================================= #
def test_save_creates_file_0600(tmp_path) -> None:
    import os

    fp = DeviceFingerprint(
        str(tmp_path / "sub" / "fp.json"),
        device_identity=_Identity())  # type: ignore[arg-type]
    fp._save({"machine_id_hash": "sha256:y"})
    assert Path(fp._path).is_file()
    assert (os.stat(fp._path).st_mode & 0o777) == 0o600


def test_save_oserror_swallowed(tmp_path) -> None:
    blocker = tmp_path / "blk"
    blocker.write_text("x")
    fp = DeviceFingerprint(
        str(blocker / "fp.json"),
        device_identity=_Identity())  # type: ignore[arg-type]
    fp._save({"k": "v"})  # OSError logged, swallowed


# ========================================================= #
# FingerprintState dataclass
# ========================================================= #
def test_fingerprint_state_defaults() -> None:
    s = FingerprintState(
        machine_id_hash="sha256:z",
        first_seen=1.0, last_verified=2.0)
    assert s.is_new is False
    assert s.mismatch is False
