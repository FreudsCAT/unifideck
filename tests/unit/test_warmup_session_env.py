"""Tests for the install-time session-env graft (prefix_warmup).

Root cause of the recurring fresh-install hang: the Decky backend is a
headless service whose environment has NO user session vars (DISPLAY,
WAYLAND_DISPLAY, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS). winetricks/
vcredist under ntsync-era Protons hangs or fails without them — while the
SAME command with those four vars restored completes in ~55s (proven A/B
on-device). Warmup now borrows them from the running Steam client and
merges with ``setdefault`` so launch-provided values are never clobbered.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from unifideck.services.download import prefix_warmup as warmup_mod


# ── _session_env_from_environ (pure parser) ─────────────────────


def test_parser_extracts_only_session_keys():
    blob = (
        b"DISPLAY=:0\0WAYLAND_DISPLAY=wayland-0\0"
        b"XDG_RUNTIME_DIR=/run/user/1000\0"
        b"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\0"
        b"HOME=/home/deck\0PATH=/usr/bin\0SECRET_TOKEN=hunter2\0"
    )
    env = warmup_mod._session_env_from_environ(blob)
    assert env == {
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }


def test_parser_skips_empty_values_and_junk():
    blob = b"DISPLAY=\0\0=nokey\0garbage\0WAYLAND_DISPLAY=wayland-1\0"
    env = warmup_mod._session_env_from_environ(blob)
    # Empty DISPLAY dropped; junk ignored; the one real var survives.
    assert env == {"WAYLAND_DISPLAY": "wayland-1"}


# ── _run_setup grafts the session env into plan.env ─────────────


@pytest.fixture
def setup_wiring(monkeypatch):
    """Wire _run_setup's lazy imports to inert stubs; capture the plan."""
    import contextlib

    from unifideck.launcher import proton as proton_pkg
    from unifideck.launcher.proton import compat as compat_pkg
    from unifideck.launcher.proton.compat import prefix_init as prefix_init_mod

    plans = []

    def _prepare(ctx, state, **kw):
        plan = SimpleNamespace(
            tool_id=kw["proton_tool_id"],
            env={"PROTONPATH": "/p", "DISPLAY": ":9"},  # launch-provided DISPLAY
        )
        plans.append(plan)
        return plan

    monkeypatch.setattr(proton_pkg, "proton_prepare", _prepare)
    monkeypatch.setattr(
        prefix_init_mod, "ensure_prefix_initialized", AsyncMock(),
    )

    async def _compat(plan):
        return False

    monkeypatch.setattr(compat_pkg, "apply_prefix_compat", _compat)
    return plans


async def test_run_setup_grafts_missing_session_vars(setup_wiring, monkeypatch):
    monkeypatch.setattr(
        warmup_mod, "_user_session_env",
        lambda: {
            "DISPLAY": ":0",
            "XDG_RUNTIME_DIR": "/run/user/1000",
        },
    )

    await warmup_mod._run_setup(
        object(), object(), "/usr/bin/python3", ("/p", "GE-Proton11-1"), "gog:1",
    )

    plan = setup_wiring[0]
    # Missing var grafted in…
    assert plan.env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    # …but an already-present value is NEVER clobbered (setdefault).
    assert plan.env["DISPLAY"] == ":9"


async def test_run_setup_survives_empty_session_env(setup_wiring, monkeypatch):
    # No Steam running, no /run/user dir — graft resolves nothing, setup
    # still proceeds (best-effort, matches pre-fix behavior).
    monkeypatch.setattr(warmup_mod, "_user_session_env", dict)

    await warmup_mod._run_setup(
        object(), object(), "/usr/bin/python3", ("/p", "GE-Proton11-1"), "gog:1",
    )

    assert setup_wiring[0].env["PROTONPATH"] == "/p"
