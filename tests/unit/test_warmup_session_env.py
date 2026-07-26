"""Tests for the install-time session-env graft.

Root cause of the recurring fresh-install hang: the Decky backend is a
headless service whose environment has NO user session vars (DISPLAY,
WAYLAND_DISPLAY, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, XAUTHORITY).
winetricks/vcredist under ntsync-era Protons hangs or fails without them —
while the SAME command with those vars restored completes in ~55s (proven
A/B on-device). Warmup borrows them from the running Steam client (the pure
parser ``_session_env_from_environ`` lives in ``prefix_warmup``) and passes
them into the canonical ``setup_prefix``/``_run_one`` (in ``prefix_setup``),
which merges with ``setdefault`` so launch-provided values are never clobbered.

``XAUTHORITY`` was missing from the original list, and its absence brought
the hang back in a subtler form: plugin_loader runs as ROOT, so a grafted
``DISPLAY=:0`` with no auth cookie means every X connection is refused
("Authorization required, but no authorization protocol specified" in
game.log) and wine blocks until the 120s compat-step killpg — twice per
install, reported only as a timeout.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from unifideck.launcher.proton import prefix_setup as setup_mod
from unifideck.services.download import prefix_warmup as warmup_mod


# ── _session_env_from_environ (pure parser) ─────────────────────


def test_parser_extracts_only_session_keys():
    blob = (
        b"DISPLAY=:0\0WAYLAND_DISPLAY=wayland-0\0"
        b"XDG_RUNTIME_DIR=/run/user/1000\0"
        b"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\0"
        b"XAUTHORITY=/run/user/1000/xauth_EyYBiQ\0"
        b"HOME=/home/deck\0PATH=/usr/bin\0SECRET_TOKEN=hunter2\0"
    )
    env = warmup_mod._session_env_from_environ(blob)
    assert env == {
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "XAUTHORITY": "/run/user/1000/xauth_EyYBiQ",
    }


def test_parser_extracts_xauthority():
    """Without the auth cookie, root's X connections are refused and wine
    blocks until the compat-step timeout — the graft is incomplete without it.
    """
    blob = b"DISPLAY=:0\0XAUTHORITY=/run/user/1000/xauth_abc123\0"
    env = warmup_mod._session_env_from_environ(blob)
    assert env["XAUTHORITY"] == "/run/user/1000/xauth_abc123"
    assert "XAUTHORITY" in warmup_mod._SESSION_ENV_KEYS


def test_parser_skips_empty_values_and_junk():
    blob = b"DISPLAY=\0\0=nokey\0garbage\0WAYLAND_DISPLAY=wayland-1\0"
    env = warmup_mod._session_env_from_environ(blob)
    # Empty DISPLAY dropped; junk ignored; the one real var survives.
    assert env == {"WAYLAND_DISPLAY": "wayland-1"}


# ── _run_one grafts the session env into plan.env ───────────────


@pytest.fixture
def setup_wiring(monkeypatch):
    """Wire _run_one's lazy imports to inert stubs; capture the plan."""
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


def _ctx():
    return SimpleNamespace(game_key="gog:1")


async def test_run_one_grafts_missing_session_vars(setup_wiring):
    # The caller (warmup) resolves the session env and passes it in; _run_one
    # merges it into plan.env with setdefault.
    await setup_mod._run_one(
        _ctx(), SimpleNamespace(), "/usr/bin/python3", ("/p", "GE-Proton11-1"),
        {"DISPLAY": ":0", "XDG_RUNTIME_DIR": "/run/user/1000"},
    )

    plan = setup_wiring[0]
    # Missing var grafted in…
    assert plan.env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    # …but an already-present value is NEVER clobbered (setdefault).
    assert plan.env["DISPLAY"] == ":9"


async def test_run_one_survives_empty_session_env(setup_wiring):
    # No Steam running, no /run/user dir — session_env is empty, setup still
    # proceeds (best-effort, matches pre-fix behavior).
    await setup_mod._run_one(
        _ctx(), SimpleNamespace(), "/usr/bin/python3", ("/p", "GE-Proton11-1"), {},
    )

    assert setup_wiring[0].env["PROTONPATH"] == "/p"
