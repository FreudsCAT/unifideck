"""Unit tests for latest-GE-Proton install + selector default tier.

Covers the three pieces added for "always run the latest GE-Proton,
fall back to Proton Experimental":

* ``ge_installer`` — latest-tag lookup, broken-extract detection,
  marker cache, and the "already installed → no download" short-circuit.
* ``selector`` — official-tool dir aliasing (so ``proton_experimental``
  resolves to ``Proton - Experimental``) and the new default tier
  (cached latest GE → on-demand download → Experimental → raise).
* ``ProtonService`` — no longer forces a per-store compat tool by
  default, but still honours an explicit ctor override.
"""
from __future__ import annotations

import json
import stat
import urllib.error
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from unifideck.launcher.proton.infrastructure import ge_installer, selector
from unifideck.launcher.types.errors import ProtonUnavailableError


def _make_proton(dir_path: Path, *, executable: bool) -> Path:
    """Create ``<dir_path>/proton`` with/without the +x bit."""
    dir_path.mkdir(parents=True, exist_ok=True)
    proton = dir_path / "proton"
    proton.write_text("#!/bin/sh\n")
    mode = proton.stat().st_mode
    exec_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    proton.chmod(mode | exec_bits if executable else mode & ~exec_bits)
    return proton


# ── ge_installer.is_valid_ge_install ──────────────────────────────

def test_is_valid_ge_install_true_when_executable(tmp_path, monkeypatch):
    root = tmp_path / "compatibilitytools.d"
    proton = _make_proton(root / "GE-Proton10-34", executable=True)
    monkeypatch.setattr(ge_installer, "_SCAN_ROOTS", (str(root),))

    assert ge_installer.is_valid_ge_install("GE-Proton10-34") is True
    assert ge_installer.installed_ge_proton_path("GE-Proton10-34") == proton


def test_is_valid_ge_install_false_when_not_executable(tmp_path, monkeypatch):
    # Mirrors the real broken GE-Proton10-34 on disk (proton is 0644):
    # present but non-executable → must be treated as NOT installed.
    root = tmp_path / "compatibilitytools.d"
    _make_proton(root / "GE-Proton10-34", executable=False)
    monkeypatch.setattr(ge_installer, "_SCAN_ROOTS", (str(root),))

    assert ge_installer.is_valid_ge_install("GE-Proton10-34") is False
    assert ge_installer.installed_ge_proton_path("GE-Proton10-34") is None


def test_is_valid_ge_install_false_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(ge_installer, "_SCAN_ROOTS", (str(tmp_path),))
    assert ge_installer.is_valid_ge_install("GE-Proton99-99") is False


# ── ge_installer.get_latest_ge_tag ────────────────────────────────

def test_get_latest_ge_tag_success():
    payload = json.dumps({"tag_name": "GE-Proton10-34"}).encode()
    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = payload
        assert ge_installer.get_latest_ge_tag() == "GE-Proton10-34"


def test_get_latest_ge_tag_network_failure_returns_none():
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("offline"),
    ):
        assert ge_installer.get_latest_ge_tag() is None


# ── ge_installer marker + ensure_latest_ge ────────────────────────

def test_marker_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ge_installer, "_MARKER", tmp_path / "latest.json")
    assert ge_installer.read_cached_latest_tag() is None
    ge_installer._write_marker("GE-Proton10-34")
    assert ge_installer.read_cached_latest_tag() == "GE-Proton10-34"


def test_ensure_latest_ge_uses_existing_without_download(tmp_path, monkeypatch):
    monkeypatch.setattr(ge_installer, "_MARKER", tmp_path / "latest.json")
    existing = tmp_path / "GE-Proton10-34" / "proton"
    monkeypatch.setattr(
        ge_installer, "_fetch_latest_release",
        lambda timeout: {"tag_name": "GE-Proton10-34"},
    )
    monkeypatch.setattr(
        ge_installer, "installed_ge_proton_path", lambda tag: existing,
    )
    download = MagicMock()
    monkeypatch.setattr(ge_installer, "_download_and_install", download)

    assert ge_installer.ensure_latest_ge() == (existing, "GE-Proton10-34")
    download.assert_not_called()
    # Marker refreshed so the launcher can skip the network next time.
    assert ge_installer.read_cached_latest_tag() == "GE-Proton10-34"


def test_ensure_latest_ge_offline_returns_none(monkeypatch):
    monkeypatch.setattr(ge_installer, "_fetch_latest_release", lambda timeout: None)
    assert ge_installer.ensure_latest_ge() is None


# ── selector.resolve_proton_path (official-tool aliasing) ─────────

def _point_selector_roots(tmp_path, monkeypatch):
    compat = tmp_path / "compat"
    lib = tmp_path / "common"
    empty = tmp_path / "empty"
    for d in (compat, lib, empty):
        d.mkdir()
    monkeypatch.setattr(selector, "UNIFIDECK_COMPAT_DIR", str(empty))
    monkeypatch.setattr(selector, "STEAM_COMPAT_ROOTS", [str(compat)])
    monkeypatch.setattr(selector, "STEAM_LIBRARY_ROOTS", [str(lib)])
    return compat, lib


def test_resolve_proton_path_aliases_experimental(tmp_path, monkeypatch):
    _compat, lib = _point_selector_roots(tmp_path, monkeypatch)
    proton = _make_proton(lib / "Proton - Experimental", executable=True)
    # The tool id is ``proton_experimental`` but the dir is the display
    # name — without the alias map this returned None (the original bug).
    assert selector.resolve_proton_path("proton_experimental") == proton


def test_resolve_proton_path_ge_in_compat_root(tmp_path, monkeypatch):
    compat, _lib = _point_selector_roots(tmp_path, monkeypatch)
    proton = _make_proton(compat / "GE-Proton10-34", executable=True)
    assert selector.resolve_proton_path("GE-Proton10-34") == proton


def test_resolve_proton_path_unknown_returns_none(tmp_path, monkeypatch):
    _point_selector_roots(tmp_path, monkeypatch)
    assert selector.resolve_proton_path("does-not-exist") is None


# ── selector.select_proton_version (default tier) ─────────────────

def _silence_higher_tiers(monkeypatch):
    monkeypatch.setattr(selector, "get_saved_proton_tool", lambda gid: None)
    monkeypatch.setattr(selector, "get_steam_compat_tool_override", lambda aid: None)
    monkeypatch.setattr(selector, "get_unifideck_proton_tool", lambda: None)


def test_select_prefers_cached_latest_ge(tmp_path, monkeypatch):
    _silence_higher_tiers(monkeypatch)
    proton = tmp_path / "GE-Proton10-34" / "proton"
    monkeypatch.setattr(
        selector.ge_installer, "read_cached_latest_tag", lambda: "GE-Proton10-34",
    )
    monkeypatch.setattr(
        selector.ge_installer, "installed_ge_proton_path",
        lambda tag: proton if tag == "GE-Proton10-34" else None,
    )
    ensure = MagicMock()
    monkeypatch.setattr(selector.ge_installer, "ensure_latest_ge", ensure)

    assert selector.select_proton_version() == (proton, "GE-Proton10-34")
    ensure.assert_not_called()  # cached → no network/download


def test_select_downloads_latest_when_not_cached(tmp_path, monkeypatch):
    _silence_higher_tiers(monkeypatch)
    proton = tmp_path / "GE-Proton10-34" / "proton"
    monkeypatch.setattr(selector.ge_installer, "read_cached_latest_tag", lambda: None)
    monkeypatch.setattr(
        selector.ge_installer, "ensure_latest_ge",
        lambda progress_cb=None: (proton, "GE-Proton10-34"),
    )
    assert selector.select_proton_version() == (proton, "GE-Proton10-34")


def test_select_falls_back_to_experimental_when_offline(tmp_path, monkeypatch):
    _silence_higher_tiers(monkeypatch)
    _compat, lib = _point_selector_roots(tmp_path, monkeypatch)
    exp = _make_proton(lib / "Proton - Experimental", executable=True)
    monkeypatch.setattr(selector.ge_installer, "read_cached_latest_tag", lambda: None)
    monkeypatch.setattr(
        selector.ge_installer, "ensure_latest_ge", lambda progress_cb=None: None,
    )

    assert selector.select_proton_version() == (exp, "proton_experimental")


def test_select_raises_when_nothing_available(tmp_path, monkeypatch):
    _silence_higher_tiers(monkeypatch)
    _point_selector_roots(tmp_path, monkeypatch)  # no Experimental on disk
    monkeypatch.setattr(selector.ge_installer, "read_cached_latest_tag", lambda: None)
    monkeypatch.setattr(
        selector.ge_installer, "ensure_latest_ge", lambda progress_cb=None: None,
    )

    with pytest.raises(ProtonUnavailableError):
        selector.select_proton_version()


# ── launch-time GE-download toasts ────────────────────────────────

def test_download_announcer_toasts_once(monkeypatch):
    """The progress callback toasts on the first chunk, then stays quiet."""
    import unifideck.launcher.frontend_bridge as fb
    spy = MagicMock()
    monkeypatch.setattr(fb, "launcher_toast", spy)

    cb = selector._GeDownloadAnnouncer()
    cb(1024, 9999)
    cb(2048, 9999)
    cb(4096, 9999)

    assert cb.fired is True
    spy.assert_called_once()
    assert spy.call_args.args[0] == "toasts.launcher.downloadingProton"


def test_select_toasts_when_download_happens(tmp_path, monkeypatch):
    """A real launch-time download fires download + ready toasts."""
    _silence_higher_tiers(monkeypatch)
    import unifideck.launcher.frontend_bridge as fb
    spy = MagicMock()
    monkeypatch.setattr(fb, "launcher_toast", spy)
    proton = tmp_path / "GE-Proton10-34" / "proton"
    monkeypatch.setattr(selector.ge_installer, "read_cached_latest_tag", lambda: None)

    def _ensure(progress_cb=None):
        # Simulate streaming bytes so the announcer fires.
        if progress_cb:
            progress_cb(1024, 2048)
        return proton, "GE-Proton10-34"

    monkeypatch.setattr(selector.ge_installer, "ensure_latest_ge", _ensure)

    assert selector.select_proton_version() == (proton, "GE-Proton10-34")
    keys = [c.args[0] for c in spy.call_args_list]
    assert "toasts.launcher.downloadingProton" in keys
    assert "toasts.launcher.protonReadyBody" in keys


def test_select_silent_when_no_download(tmp_path, monkeypatch):
    """No download (cb never fires) → no GE toasts."""
    _silence_higher_tiers(monkeypatch)
    import unifideck.launcher.frontend_bridge as fb
    spy = MagicMock()
    monkeypatch.setattr(fb, "launcher_toast", spy)
    proton = tmp_path / "GE-Proton10-34" / "proton"
    monkeypatch.setattr(selector.ge_installer, "read_cached_latest_tag", lambda: None)
    # Already-installed path: ensure_latest_ge returns without streaming.
    monkeypatch.setattr(
        selector.ge_installer, "ensure_latest_ge",
        lambda progress_cb=None: (proton, "GE-Proton10-34"),
    )

    selector.select_proton_version()
    keys = [c.args[0] for c in spy.call_args_list]
    assert "toasts.launcher.downloadingProton" not in keys
    assert "toasts.launcher.protonReadyBody" not in keys


# ── umu runtime first-setup toast ─────────────────────────────────

def test_umu_runtime_toasts_when_steamrt3_missing(tmp_path, monkeypatch):
    from unifideck.launcher.proton.infrastructure import umu_runtime
    spy = MagicMock()
    monkeypatch.setattr(umu_runtime, "launcher_toast", spy)
    monkeypatch.setattr(umu_runtime, "UMU_CACHE_DIR", tmp_path / "umu")
    monkeypatch.setenv("HOME", str(tmp_path))  # contain ~/.config/umu

    umu_runtime.ensure_umu_runtime_ready()

    spy.assert_called_once()
    assert spy.call_args.args[0] == "toasts.launcher.downloadingRuntime"


def test_umu_runtime_silent_when_steamrt3_present(tmp_path, monkeypatch):
    from unifideck.launcher.proton.infrastructure import umu_runtime
    spy = MagicMock()
    monkeypatch.setattr(umu_runtime, "launcher_toast", spy)
    cache = tmp_path / "umu"
    (cache / "steamrt3").mkdir(parents=True)
    monkeypatch.setattr(umu_runtime, "UMU_CACHE_DIR", cache)
    monkeypatch.setenv("HOME", str(tmp_path))

    umu_runtime.ensure_umu_runtime_ready()

    spy.assert_not_called()


# ── ProtonService default-tool policy ─────────────────────────────

async def test_proton_service_no_force_by_default():
    from unifideck.services.proton_service import ProtonService

    with patch("unifideck.services.proton_service.auto_wire"):
        svc = ProtonService(MagicMock(), "/nonexistent/config.vdf")
    svc.set_compat_tool = AsyncMock()

    await svc._on_game_installed(store="epic", app_id=12345)
    svc.set_compat_tool.assert_not_awaited()


async def test_proton_service_override_still_forces_tool():
    from unifideck.services.proton_service import ProtonService

    with patch("unifideck.services.proton_service.auto_wire"):
        svc = ProtonService(
            MagicMock(), "/nonexistent/config.vdf",
            overrides={"epic": "GE-Proton10-34"},
        )
    svc.set_compat_tool = AsyncMock()

    await svc._on_game_installed(store="epic", app_id=999)
    svc.set_compat_tool.assert_awaited_once_with(999, "GE-Proton10-34")


# ── emit_stage payload forwarding ─────────────────────────────────

async def test_emit_stage_forwards_optional_fields():
    from unifideck.core.types import Events
    from unifideck.launcher.rpc import emit_stage

    bus = MagicMock()
    bus.emit = AsyncMock()
    await emit_stage(
        bus,
        i18n_key="toasts.launcher.protonSwitchedTo",
        i18n_title_key="toasts.launcher.protonUpgrade",
        game_title="epic:1",
        i18n_params={"version": "GE-Proton10-34"},
        severity="info",
        priority="normal",
    )
    bus.emit.assert_awaited_once()
    args, kwargs = bus.emit.call_args
    assert args[0] == Events.LAUNCHER_STAGE
    assert kwargs["i18n_title_key"] == "toasts.launcher.protonUpgrade"
    assert kwargs["i18n_key"] == "toasts.launcher.protonSwitchedTo"
    assert kwargs["i18n_params"] == {"version": "GE-Proton10-34"}
    assert kwargs["severity"] == "info"


async def test_emit_stage_omits_unset_optionals():
    from unifideck.launcher.rpc import emit_stage

    bus = MagicMock()
    bus.emit = AsyncMock()
    await emit_stage(bus, i18n_key="toasts.launcher.launchingGame", game_title="g")
    _args, kwargs = bus.emit.call_args
    assert "i18n_title_key" not in kwargs
    assert "i18n_params" not in kwargs
    assert "severity" not in kwargs
