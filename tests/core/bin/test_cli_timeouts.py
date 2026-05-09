"""Tests for core/bin/cli_timeouts.py (OP-07c)."""
from __future__ import annotations

from tests.helpers import MockConfig
from unifideck.core.binaries.cli_timeouts import DEFAULT_TIMEOUTS, read_cli_timeouts


def test_none_config_returns_defaults():
    result = read_cli_timeouts(None)
    assert result == DEFAULT_TIMEOUTS


def test_defaults_not_mutated():
    """read_cli_timeouts should return a copy, not the original dict."""
    result = read_cli_timeouts(None)
    result["auth_check"] = 999
    assert DEFAULT_TIMEOUTS["auth_check"] == 10


def test_partial_override():
    cfg = MockConfig({"cli_timeouts": {"auth_check": 20}})
    result = read_cli_timeouts(cfg)
    assert result["auth_check"] == 20
    assert result["version_check"] == 2  # unchanged default


def test_non_int_ignored():
    cfg = MockConfig({"cli_timeouts": {"auth_check": "not_int"}})
    result = read_cli_timeouts(cfg)
    assert result["auth_check"] == 10  # falls back to default


def test_negative_ignored():
    cfg = MockConfig({"cli_timeouts": {"auth_check": -5}})
    result = read_cli_timeouts(cfg)
    assert result["auth_check"] == 10


def test_all_default_keys_present():
    result = read_cli_timeouts(None)
    for key in ("auth_check", "version_check", "library_fetch", "install_poll", "uninstall"):
        assert key in result
