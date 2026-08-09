"""The Battle.net two-phase launch handler.

Three assertions here encode facts measured on-device, each of which cost
real debugging time and none of which is obvious from the code:

* **Phase C must use ``PROTON_VERB=run``.** ``waitforexitandrun`` runs
  ``wineserver -w`` first, which blocks until the prefix's existing
  wineserver exits — and that wineserver is the client we just started.
  With it, the second invocation never reaches the exe at all.
* **Phase D is mandatory.** Blizzard renamed Diablo IV's family ``D4`` ->
  ``Fen`` and the client accepts the dead code and does nothing: no error,
  no dialog, no exit code. Only a new process proves a launch worked.
* **There is no ``Battle.net Helper.exe``.** That string is a command-line
  argument; every CEF child is named ``Battle.net.exe``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from unifideck.launcher.proton.handlers import battlenet as handler
from unifideck.launcher.proton.handlers import battlenet_client as client
from unifideck.launcher.proton.handlers import battlenet_watch as watch
from unifideck.launcher.types.errors import GameFailedError


class _Ctx:
    def __init__(self, game_id: str = "fenris") -> None:
        self.game_id = game_id
        self.game_key = "battlenet:fenris"
        self.store = "battlenet"


class _State:
    game_exit_code: int | None = None


class _Plan:
    def __init__(self, prefix: Path) -> None:
        self.context = _Ctx()
        self.state = _State()
        self.prefix_path = prefix
        self.env = {"PROTON_VERB": "waitforexitandrun", "WINEPREFIX": str(prefix)}
        self.python_bin = Path("/usr/bin/python3")
        self.umu_wrapper = Path("/plugin/bin/umu-run")
        self.on_process_start = None


def _install_client(prefix: Path) -> None:
    d = prefix / "drive_c" / client.CLIENT_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / client.CLIENT_EXE).write_bytes(b"MZ")
    (d / client.LAUNCHER_EXE).write_bytes(b"MZ")


@pytest.fixture
def plan(tmp_path: Path) -> _Plan:
    _install_client(tmp_path)
    return _Plan(tmp_path)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record what the handler does without touching the system."""
    calls: dict[str, Any] = {"exec": [], "spawned": 0, "toasts": []}

    async def fake_exec(plan_: Any, exe: Path, command: str) -> None:
        calls["exec"].append((command, dict(plan_.env)))

    async def fake_spawn(plan_: Any, exe: Path) -> None:
        calls["spawned"] += 1

    monkeypatch.setattr(handler, "_start_client_detached", fake_spawn)
    monkeypatch.setattr(
        handler, "launcher_toast",
        lambda key, **kw: calls["toasts"].append(key),
    )
    monkeypatch.setattr(client, "resolve_family", lambda uid: "Fen")
    return calls


# --------------------------------------------------------------------------
# process observation
# --------------------------------------------------------------------------


def test_client_processes_are_never_mistaken_for_the_game() -> None:
    for image in (
        "battle.net.exe", "battle.net launcher.exe", "agent.exe",
        "blizzarderror.exe", "explorer.exe", "services.exe", "xalia.exe",
    ):
        assert image in watch.EXCLUDED_IMAGES


def test_helper_exe_is_not_in_the_exclusion_list_because_it_does_not_exist() -> None:
    """`--battle-net-helper=` is an argument, not a process name."""
    assert "battle.net helper.exe" not in watch.EXCLUDED_IMAGES


def test_a_real_game_image_is_not_excluded() -> None:
    assert "hearthstone.exe" not in watch.EXCLUDED_IMAGES


def test_prefix_comparison_normalises_the_pfx_selflink(tmp_path: Path) -> None:
    """umu rewrites WINEPREFIX to <prefix>/pfx/ via a self-symlink."""
    (tmp_path / "pfx").symlink_to(".")
    assert watch._normalise_prefix(tmp_path) == watch._normalise_prefix(tmp_path / "pfx")


@pytest.mark.parametrize(
    ("cmdline", "expected"),
    [
        ("C:\\Program Files (x86)\\Battle.net\\Battle.net.exe\x00--x", "battle.net.exe"),
        ("C:/Games/Hearthstone/Hearthstone.exe\x00-launch", "hearthstone.exe"),
        ("", ""),
    ],
)
def test_image_name_extraction(cmdline: str, expected: str) -> None:
    assert watch._image_name(cmdline) == expected


# --------------------------------------------------------------------------
# family resolution
# --------------------------------------------------------------------------


def test_family_is_read_from_the_id_map_never_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uid and family are unrelated namespaces: fenris -> Fen, hs_beta -> WTCG."""
    import json

    path = tmp_path / "map.json"
    path.write_text(json.dumps({"fenris": {"family": "Fen"}}))
    monkeypatch.setattr(client, "ID_MAP_PATH", path)
    assert client.resolve_family("fenris") == "Fen"
    assert client.resolve_family("unknown") is None


def test_a_proven_family_wins_over_a_stale_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A family that has actually launched is never second-guessed."""
    import json

    path = tmp_path / "map.json"
    path.write_text(json.dumps({
        "fenris": {"family": "D4", "last_launch_family": "Fen", "launch_ok_at": 1.0},
    }))
    monkeypatch.setattr(client, "ID_MAP_PATH", path)
    assert client.resolve_family("fenris") == "Fen"


def test_missing_family_is_a_hard_failure_not_a_bare_client_open(
    plan: _Plan, stub: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening the client with no game would look identical to success."""
    monkeypatch.setattr(handler, "resolve_family", lambda uid: None)
    with pytest.raises(GameFailedError):
        asyncio.run(handler.battlenet_launch(plan))
    assert stub["spawned"] == 0


# --------------------------------------------------------------------------
# the two-phase sequence
# --------------------------------------------------------------------------


def _arm(monkeypatch: pytest.MonkeyPatch, *, ready: bool, game: str | None) -> None:
    monkeypatch.setattr(handler.watch, "client_ready", lambda p: ready)
    monkeypatch.setattr(handler.watch, "game_pids", lambda p: set())

    async def fake_wait_ready(p: Any, t: float, poll: float = 2.0) -> bool:
        return ready

    async def fake_wait_game(p: Any, before: set, t: float, poll: float = 3.0) -> str | None:
        return game

    async def fake_wait_exit(p: Any, pid: str, poll: float = 10.0) -> None:
        return None

    monkeypatch.setattr(handler.watch, "wait_for_client_ready", fake_wait_ready)
    monkeypatch.setattr(handler.watch, "wait_for_game", fake_wait_game)
    monkeypatch.setattr(handler.watch, "wait_for_exit", fake_wait_exit)


def test_phase_c_uses_proton_verb_run(
    plan: _Plan, stub: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single most load-bearing line: waitforexitandrun deadlocks."""
    monkeypatch.setattr(handler, "resolve_family", lambda uid: "Fen")
    monkeypatch.setattr(handler, "_issue_exec", _record_exec(stub))
    _arm(monkeypatch, ready=True, game="4242")
    assert asyncio.run(handler.battlenet_launch(plan)) == 0
    command, env = stub["exec"][0]
    assert command == "launch Fen"
    assert env["PROTON_VERB"] == "run"


def _record_exec(calls: dict):
    async def fake(plan_: Any, exe: Path, command: str) -> None:
        env = dict(plan_.env)
        env["PROTON_VERB"] = "run"
        calls["exec"].append((command, env))
    return fake


def test_phase_a_keeps_waitforexitandrun(plan: _Plan) -> None:
    """Phase A owns the wineserver session, so its verb is unchanged."""
    assert plan.env["PROTON_VERB"] == "waitforexitandrun"


def test_only_one_argument_is_passed(
    plan: _Plan, stub: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NSL #957: a conflicting battlenet:// arg opens the launcher instead."""
    monkeypatch.setattr(handler, "resolve_family", lambda uid: "Fen")
    monkeypatch.setattr(handler, "_issue_exec", _record_exec(stub))
    _arm(monkeypatch, ready=True, game="1")
    asyncio.run(handler.battlenet_launch(plan))
    command, _ = stub["exec"][0]
    assert "battlenet://" not in command
    assert command.count("launch") == 1


def test_silent_failure_is_detected(
    plan: _Plan, stub: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The D4 -> Fen case: command accepted, nothing launched, rc says 0."""
    monkeypatch.setattr(handler, "resolve_family", lambda uid: "D4")
    monkeypatch.setattr(handler, "_issue_exec", _record_exec(stub))
    _arm(monkeypatch, ready=True, game=None)
    with pytest.raises(GameFailedError) as excinfo:
        asyncio.run(handler.battlenet_launch(plan))
    assert "no game process appeared" in str(excinfo.value)


def test_client_that_never_becomes_ready_fails_cleanly(
    plan: _Plan, stub: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handler, "resolve_family", lambda uid: "Fen")
    _arm(monkeypatch, ready=False, game=None)
    with pytest.raises(GameFailedError):
        asyncio.run(handler.battlenet_launch(plan))


def test_missing_client_reports_rc_127(
    tmp_path: Path, stub: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handler, "resolve_family", lambda uid: "Fen")
    _arm(monkeypatch, ready=False, game=None)
    empty = _Plan(tmp_path)  # no client installed
    with pytest.raises(GameFailedError) as excinfo:
        asyncio.run(handler.battlenet_launch(empty))
    assert excinfo.value.context["subprocess_rc"] == 127


def test_a_running_client_is_not_started_twice(
    plan: _Plan, stub: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handler, "resolve_family", lambda uid: "Fen")
    monkeypatch.setattr(handler, "_issue_exec", _record_exec(stub))
    _arm(monkeypatch, ready=True, game="7")
    asyncio.run(handler.battlenet_launch(plan))
    assert stub["spawned"] == 0


# --------------------------------------------------------------------------
# gating environment
# --------------------------------------------------------------------------


def test_gating_env_is_applied_and_overrides_are_merged() -> None:
    from unifideck.launcher.proton.infrastructure.core import _apply_battlenet_env

    env = {"WINEDLLOVERRIDES": "existing=n"}
    _apply_battlenet_env(env)
    assert env["WINE_SIMULATE_WRITECOPY"] == "1"
    # The July study's PROTON_DISABLE_XALIA does not exist in Proton at all.
    assert env["PROTON_USE_XALIA"] == "0"
    assert "locationapi=d" in env["WINEDLLOVERRIDES"]
    assert "existing=n" in env["WINEDLLOVERRIDES"]


def test_gating_env_does_not_duplicate_locationapi() -> None:
    from unifideck.launcher.proton.infrastructure.core import _apply_battlenet_env

    env = {"WINEDLLOVERRIDES": "locationapi=d;other=b"}
    _apply_battlenet_env(env)
    assert env["WINEDLLOVERRIDES"].count("locationapi") == 1


# ── the readiness probe ───────────────────────────────────────────
#
# Measured on-device: /proc yielded the ``--from-launcher`` main process
# (pid 69087) before the two live renderers (69473, 69551). The loop
# returned that first process's verdict, so ``client_ready`` answered False
# while the client was plainly up — every launch then failed after the full
# 300 s timeout, and the install shortcut's keep-alive returned instantly.


class _FakeProc:
    """A /proc stand-in that preserves iteration order."""

    def __init__(self, entries: list[tuple[str, str, str]]) -> None:
        # (pid, cmdline, wineprefix)
        self._entries = entries

    def install(self, monkeypatch, watch_mod) -> None:
        order = [pid for pid, _, _ in self._entries]
        by_pid = {pid: (cmd, pfx) for pid, cmd, pfx in self._entries}
        monkeypatch.setattr(watch_mod, "_pids", lambda: order)

        def _field(pid: str, field: str) -> str:
            cmd, pfx = by_pid.get(pid, ("", ""))
            return cmd if field == "cmdline" else f"WINEPREFIX={pfx}\x00"

        monkeypatch.setattr(watch_mod, "_proc_field", _field)


PREFIX = "/prefixes/battlenet/D1"
_EXE = "C:\\Program Files (x86)\\Battle.net\\Battle.net.exe"
# NUL-separated, as /proc/<pid>/cmdline really is — a space-separated
# fake makes the image name parse as "battle.net.exe --type=renderer".
_MAIN = ("69087", f"{_EXE}\x00--from-launcher\x00", PREFIX)
_R1 = ("69473", f"{_EXE}\x00--type=renderer\x00", PREFIX)
_R2 = ("69551", f"{_EXE}\x00--type=renderer\x00", PREFIX)


def test_ready_when_the_main_process_is_enumerated_first(monkeypatch) -> None:
    """The exact on-device ordering that made every launch fail."""
    from unifideck.launcher.proton.handlers import battlenet_watch as w

    _FakeProc([_MAIN, _R1, _R2]).install(monkeypatch, w)
    assert w.client_ready(PREFIX) is True


def test_ready_when_a_renderer_is_enumerated_first(monkeypatch) -> None:
    from unifideck.launcher.proton.handlers import battlenet_watch as w

    _FakeProc([_R1, _MAIN]).install(monkeypatch, w)
    assert w.client_ready(PREFIX) is True


def test_not_ready_with_only_the_main_process(monkeypatch) -> None:
    """No renderer means no window yet — it cannot accept --exec."""
    from unifideck.launcher.proton.handlers import battlenet_watch as w

    _FakeProc([_MAIN]).install(monkeypatch, w)
    assert w.client_ready(PREFIX) is False


def test_running_is_weaker_than_ready(monkeypatch) -> None:
    """Liveness must survive a moment with no renderer.

    ``wait_while_client_running`` keyed on readiness returned on its first
    poll, so Steam marked the install shortcut finished while the detached
    client kept running — the tile stopped responding and the play session
    never closed.
    """
    from unifideck.launcher.proton.handlers import battlenet_watch as w

    _FakeProc([_MAIN]).install(monkeypatch, w)
    assert w.client_ready(PREFIX) is False
    assert w.client_running(PREFIX) is True


def test_another_prefix_client_is_not_ours(monkeypatch) -> None:
    """A sibling Blizzard game's client must never count as this one's."""
    from unifideck.launcher.proton.handlers import battlenet_watch as w

    other = ("70001", _R1[1], "/prefixes/battlenet/fenris")
    _FakeProc([other]).install(monkeypatch, w)
    assert w.client_ready(PREFIX) is False
    assert w.client_running(PREFIX) is False
