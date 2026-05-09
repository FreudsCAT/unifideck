"""Tests for core/net/ssl_helpers.py (OP-08a)."""
from __future__ import annotations

import ssl

from unifideck.core.net.ssl_helpers import ssl_ctx_permissive, ssl_ctx_strict


def test_strict_returns_ssl_context():
    ctx = ssl_ctx_strict()
    assert isinstance(ctx, ssl.SSLContext)


def test_strict_is_singleton():
    assert ssl_ctx_strict() is ssl_ctx_strict()


def test_strict_verifies():
    ctx = ssl_ctx_strict()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_permissive_returns_ssl_context():
    ctx = ssl_ctx_permissive("unit test")
    assert isinstance(ctx, ssl.SSLContext)


def test_permissive_is_singleton():
    assert ssl_ctx_permissive("a") is ssl_ctx_permissive("b")


def test_permissive_disables_verification():
    ctx = ssl_ctx_permissive("test")
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
