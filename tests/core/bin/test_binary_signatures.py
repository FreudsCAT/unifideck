"""Tests for core/bin/binary_signatures.py (OP-07b)."""
from __future__ import annotations

from unifideck.core.binaries.binary_signatures import (
    _KNOWN_HASHES,
    compute_sha256,
    verify_bundled_binary,
)


def test_compute_sha256_of_known_content(tmp_path):
    f = tmp_path / "test.bin"
    f.write_bytes(b"hello")
    h = compute_sha256(str(f))
    assert h is not None
    assert len(h) == 64
    # SHA256 of "hello" is well-known
    assert h == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_compute_sha256_missing_file():
    assert compute_sha256("/nonexistent_xyz_test") is None


def test_compute_sha256_empty_file(tmp_path):
    f = tmp_path / "empty"
    f.write_bytes(b"")
    h = compute_sha256(str(f))
    assert h is not None
    assert len(h) == 64


def test_verify_unknown_tool():
    assert verify_bundled_binary("unknown_tool", "/any") is None


def test_verify_empty_hash_returns_none():
    """Tools with empty hash string (no baseline yet) return None."""
    assert verify_bundled_binary("legendary", "/any/path") is None


def test_verify_missing_binary():
    assert verify_bundled_binary("legendary", "/nonexistent") is None


def test_known_hashes_has_expected_tools():
    assert "legendary" in _KNOWN_HASHES
    assert "nile" in _KNOWN_HASHES
    assert "gogdl" in _KNOWN_HASHES
