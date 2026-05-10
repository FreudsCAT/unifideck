"""Tests for utils/config_helpers.py (OP-33c)."""
from __future__ import annotations

import json
import os

from tests.helpers import MockConfig
from unifideck.utils.config_helpers import (
    get_cfg,
    read_config_int_cold_start,
)


def test_get_cfg_with_config():
    cfg = MockConfig({"stores": {"epic": {"timeout": 30}}})
    assert get_cfg(cfg, "stores.epic.timeout", 10) == 30


def test_get_cfg_missing_key_returns_default():
    cfg = MockConfig({})
    assert get_cfg(cfg, "nonexistent.key", 42) == 42


def test_get_cfg_none_config_returns_default():
    assert get_cfg(None, "any.key", "fallback") == "fallback"


def test_get_cfg_none_value_returns_default():
    """When config returns None, get_cfg returns the default."""
    cfg = MockConfig({"key": None})
    assert get_cfg(cfg, "key", "default") == "default"


def test_cold_start_reads_json(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "launcher": {"auth_max_seconds": 120},
    }))
    monkeypatch.setattr(
        "unifideck.utils.config_helpers._COLD_START_CONFIG_PATH",
        str(config_file),
    )
    assert read_config_int_cold_start("launcher.auth_max_seconds", 60) == 120


def test_cold_start_missing_file():
    """Missing file should return default."""
    assert read_config_int_cold_start("any.key", 99) == 99


def test_cold_start_non_int_returns_default(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"val": "not_int"}))
    monkeypatch.setattr(
        "unifideck.utils.config_helpers._COLD_START_CONFIG_PATH",
        str(config_file),
    )
    assert read_config_int_cold_start("val", 5) == 5


def test_cold_start_negative_returns_default(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"val": -10}))
    monkeypatch.setattr(
        "unifideck.utils.config_helpers._COLD_START_CONFIG_PATH",
        str(config_file),
    )
    assert read_config_int_cold_start("val", 5) == 5
