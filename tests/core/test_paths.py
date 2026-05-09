"""Tests for core/paths.py (OP-04f)."""
from __future__ import annotations

import os
from pathlib import Path

from unifideck.core.paths import resolve_plugin_dir, resolve_py_modules_dir


def test_resolve_from_env(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "myplugin"
    plugin_dir.mkdir()
    monkeypatch.setenv("UNIFIDECK_PLUGIN_DIR", str(plugin_dir))
    assert resolve_plugin_dir() == plugin_dir


def test_decky_env_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("UNIFIDECK_PLUGIN_DIR", raising=False)
    plugin_dir = tmp_path / "decky_plugin"
    plugin_dir.mkdir()
    monkeypatch.setenv("DECKY_PLUGIN_DIR", str(plugin_dir))
    assert resolve_plugin_dir() == plugin_dir


def test_walk_up_finds_plugin_json(tmp_path):
    root = tmp_path / "plugin_root"
    root.mkdir()
    (root / "plugin.json").write_text("{}")
    nested = root / "a" / "b" / "c"
    nested.mkdir(parents=True)
    result = resolve_plugin_dir(start=nested)
    assert result == root


def test_fallback_returns_canonical(monkeypatch):
    monkeypatch.delenv("UNIFIDECK_PLUGIN_DIR", raising=False)
    monkeypatch.delenv("DECKY_PLUGIN_DIR", raising=False)
    result = resolve_plugin_dir()
    assert isinstance(result, Path)


def test_resolve_py_modules_dir():
    result = resolve_py_modules_dir()
    assert result.name == "py_modules"
