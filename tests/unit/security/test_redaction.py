"""Deep behavioural tests — security/redaction.py.

Source : py_modules/unifideck/security/redaction.py
Fiche  : OP   Critical (security) — coverage floor 95%.

Audit redaction: sensitive-key matching (broad substrings,
case-insensitive, non-str keys), recursive dict redaction,
long-value truncation, malformed-payload sentinel. The
redactor must NEVER raise and never mutate its input.
"""
from __future__ import annotations

from typing import Any

import pytest

import unifideck.security.redaction as RED
from unifideck.security.redaction import (
    redact_for_audit,
    _is_sensitive_key,
    _redact_value,
)

_SENTINEL = "<redacted>"


def test_module_imports() -> None:
    assert hasattr(RED, "redact_for_audit")


# ========================================================= #
# _is_sensitive_key
# ========================================================= #
@pytest.mark.parametrize("key", [
    "token", "Access_Token", "PASSWORD",
    "client_secret", "session_cookie",
    "api_key", "apikey", "bearer",
    "user_credential", "session_id",
    "xbl_token",
])
def test_sensitive_keys(key) -> None:
    assert _is_sensitive_key(key) is True


@pytest.mark.parametrize("key", [
    "username", "game_id", "count", "title",
])
def test_non_sensitive_keys(key) -> None:
    assert _is_sensitive_key(key) is False


def test_sensitive_non_str_key() -> None:
    # int key stringified -> "123" not sensitive
    assert _is_sensitive_key(123) is False


def test_sensitive_key_str_raises() -> None:
    class _Bad:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    # pathological key -> treated as sensitive
    assert _is_sensitive_key(_Bad()) is True


# ========================================================= #
# _redact_value
# ========================================================= #
def test_redact_value_sensitive() -> None:
    assert _redact_value(
        "token", "abc123") == _SENTINEL


def test_redact_value_nested_dict() -> None:
    out = _redact_value(
        "data", {"password": "p", "ok": 1})
    assert out["password"] == _SENTINEL
    assert out["ok"] == 1


def test_redact_value_long_string() -> None:
    long = "x" * 500
    out = _redact_value("note", long)
    assert "truncated 500 chars" in out


def test_redact_value_passthrough() -> None:
    assert _redact_value("count", 42) == 42


# ========================================================= #
# redact_for_audit
# ========================================================= #
def test_redact_audit_basic() -> None:
    out = redact_for_audit(
        {"access_token": "secret",
         "username": "alice"})
    assert out["access_token"] == _SENTINEL
    assert out["username"] == "alice"


def test_redact_audit_recursive() -> None:
    out = redact_for_audit(
        {"creds": {"refresh_token": "r"},
         "meta": {"count": 3}})
    assert out["creds"]["refresh_token"] == \
        _SENTINEL
    assert out["meta"]["count"] == 3


def test_redact_audit_not_dict() -> None:
    out = redact_for_audit(["not", "a", "dict"])  # type: ignore[arg-type]
    assert "<malformed_payload>" in out


def test_redact_audit_does_not_mutate() -> None:
    src = {"token": "secret", "x": 1}
    out = redact_for_audit(src)
    assert src["token"] == "secret"  # unchanged
    assert out["token"] == _SENTINEL
    assert out is not src


def test_redact_audit_long_value_truncated(
) -> None:
    out = redact_for_audit(
        {"description": "y" * 1000})
    assert "truncated 1000 chars" in \
        out["description"]


def test_redact_audit_empty() -> None:
    assert redact_for_audit({}) == {}
