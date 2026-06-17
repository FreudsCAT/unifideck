"""Regression: the Ubisoft auth shortcut must Proton-launch UPC.

The launcher's auth handler used to no-op for Ubisoft (`handle_store_auth`
returned immediately), so the Steam shortcut opened and closed at once.
Sign-in needs Ubisoft Connect (UPC) actually running in the ``.upc-auth``
prefix so the user can authenticate. These tests pin:

* ``ubisoft_auth_launch`` runs ``[python, umu, upc.exe]`` — bare, no
  ``uplay://`` URL (that form is only for launching a specific game);
* a missing upc.exe raises a clear error rather than closing silently;
* ``LauncherService._handle_auth_path`` routes Ubisoft to the Proton path
  and the OAuth stores to ``handle_store_auth``.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from unifideck.launcher.proton.handlers import ubisoft as h
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.types.errors import GameFailedError

_UPC_REL = Path(
    "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe",
)


def _plan(prefix: Path) -> ProtonLaunchPlan:
    return ProtonLaunchPlan(
        context=types.SimpleNamespace(game_id="upc-auth", store="ubisoft"),
        state=types.SimpleNamespace(game_exit_code=0),
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/plugin/bin/umu/umu/umu-run"),
        prefix_path=prefix,
        env={"WINEPREFIX": str(prefix)},
        on_process_start=None,
    )


@pytest.fixture()
def _quiet_toast(monkeypatch):
    monkeypatch.setattr(h, "launcher_toast", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_auth_launch_runs_upc_bare(tmp_path, monkeypatch, _quiet_toast):
    captured: dict[str, object] = {}

    async def fake_umu(argv, *, env=None, on_start=None, **kw):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(h, "run_umu_with_retry", fake_umu)
    upc = tmp_path / _UPC_REL
    upc.parent.mkdir(parents=True, exist_ok=True)
    upc.write_text("stub")

    rc = await h.ubisoft_auth_launch(_plan(tmp_path))

    assert rc == 0
    assert captured["argv"] == [
        "/usr/bin/python3",
        "/plugin/bin/umu/umu/umu-run",
        str(upc),
    ]
    assert not any("uplay://" in str(a) for a in captured["argv"])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_auth_launch_missing_upc_raises(tmp_path, _quiet_toast):
    with pytest.raises(GameFailedError, match=r"upc\.exe"):
        await h.ubisoft_auth_launch(_plan(tmp_path))


@pytest.mark.asyncio
async def test_handle_auth_path_routes_ubisoft_to_proton(monkeypatch):
    from unittest.mock import AsyncMock

    from unifideck.services.launcher import service as svc_mod

    svc = svc_mod.LauncherService.__new__(svc_mod.LauncherService)
    svc._launch_ubisoft_auth = AsyncMock(return_value="UBI")
    svc._edge_browser = object()

    out = await svc._handle_auth_path(types.SimpleNamespace(auth_store="ubisoft"))
    assert out == "UBI"
    svc._launch_ubisoft_auth.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_auth_path_routes_oauth_to_browser(monkeypatch):
    from unittest.mock import AsyncMock

    from unifideck.launcher.flows import auth as auth_mod
    from unifideck.services.launcher import service as svc_mod

    monkeypatch.setattr(auth_mod, "handle_store_auth", AsyncMock(return_value="OAUTH"))
    svc = svc_mod.LauncherService.__new__(svc_mod.LauncherService)
    svc._launch_ubisoft_auth = AsyncMock(return_value="UBI")
    svc._edge_browser = object()

    out = await svc._handle_auth_path(types.SimpleNamespace(auth_store="epic"))
    assert out == "OAUTH"
    svc._launch_ubisoft_auth.assert_not_awaited()
