"""Regression: native Linux games must get Steam Overlay/Input back.

``bin/unifideck-launcher`` unconditionally strips ``LD_PRELOAD`` at process
start (needed for the Proton/pressure-vessel path). The retired bash
launcher's ``restore_steam_env`` restored the real value for native Linux
games specifically ("critical for controller support"), but the 0.7.0
rewrite's ``_restore_steam_env`` only restored the ``STEAM_OVERLAY``/
``STEAM_INPUT`` feature flags and never the preload itself — so Steam's
overlay never re-attached to a native game's process. Symptom reported in
the field: game launches and is audible, but Gaming Mode's loading
transition never dismisses (no in-process Steam hook to signal it) and
Steam Input doesn't work. Native games run unsandboxed on the host, so
(unlike Proton/pressure-vessel) there's no host/container library
mismatch risk in restoring it.
"""
from __future__ import annotations

from unifideck.launcher.flows.native import _restore_steam_env


def _write_steam_env(tmp_path, monkeypatch, content: str) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    steam_dir = tmp_path / ".steam"
    steam_dir.mkdir(parents=True, exist_ok=True)
    (steam_dir / "steam.env").write_text(content)


def test_restore_steam_env_includes_ld_preload(tmp_path, monkeypatch):
    _write_steam_env(
        tmp_path, monkeypatch,
        "STEAM_OVERLAY=1\n"
        "STEAM_INPUT=1\n"
        "LD_PRELOAD=/home/deck/.local/share/Steam/ubuntu12_64/gameoverlayrenderer.so\n"
        "SOME_OTHER_VAR=ignored\n",
    )
    env: dict[str, str] = {}
    _restore_steam_env(env)

    assert env["LD_PRELOAD"] == (
        "/home/deck/.local/share/Steam/ubuntu12_64/gameoverlayrenderer.so"
    )
    assert env["STEAM_OVERLAY"] == "1"
    assert env["STEAM_INPUT"] == "1"
    assert "SOME_OTHER_VAR" not in env


def test_restore_steam_env_no_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    env: dict[str, str] = {}
    _restore_steam_env(env)
    assert env == {}
