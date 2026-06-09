"""Unit tests for the launcher→plugin toast bridge (frontend_bridge).

The launcher runs as a separate process, so its LAUNCHER_STAGE toasts
are written to a shared JSONL file and drained into the plugin's replay
buffer on each ``subscribe_replay`` poll. These tests cover the writer
(record_event / launcher_toast / cap) and the reader
(LauncherEventDrainer prime → dedup → record).
"""
from __future__ import annotations

import json

import pytest

from unifideck.launcher import frontend_bridge as fb


@pytest.fixture(autouse=True)
def _bridge_file(tmp_path, monkeypatch):
    """Point the bridge at a temp file for every test."""
    monkeypatch.setattr(fb, "EVENTS_FILE", tmp_path / "launcher_events.jsonl")


class _FakeReplay:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, dict]] = []

    def record(self, event: str, kwargs: dict) -> None:
        self.recorded.append((event, kwargs))


# ── writer ────────────────────────────────────────────────────────

def test_launcher_toast_writes_full_payload():
    fb.launcher_toast(
        "toasts.launcher.startingEpicGame",
        i18n_title_key="toasts.launcher.launchingGame",
        i18n_params={"version": "GE-Proton10-34"},
        severity="warning",
        game_title="epic:1",
    )
    line = fb.EVENTS_FILE.read_text().splitlines()[-1]
    rec = json.loads(line)
    assert rec["event"] == "launcher_stage"
    assert isinstance(rec["ts"], (int, float))
    kw = rec["kwargs"]
    assert kw["i18n_key"] == "toasts.launcher.startingEpicGame"
    assert kw["i18n_title_key"] == "toasts.launcher.launchingGame"
    assert kw["i18n_params"] == {"version": "GE-Proton10-34"}
    assert kw["severity"] == "warning"
    assert kw["game_title"] == "epic:1"


def test_launcher_toast_omits_unset_optionals():
    fb.launcher_toast("toasts.launcher.signingIn")
    kw = json.loads(fb.EVENTS_FILE.read_text().splitlines()[-1])["kwargs"]
    assert "i18n_title_key" not in kw
    assert "i18n_params" not in kw
    assert "severity" not in kw


def test_record_event_caps_file_length():
    for i in range(fb._MAX_LINES + 50):
        fb.record_event("launcher_stage", {"i": i})
    lines = fb.EVENTS_FILE.read_text().splitlines()
    assert len(lines) == fb._MAX_LINES
    # The most recent events are the ones kept.
    assert json.loads(lines[-1])["kwargs"]["i"] == fb._MAX_LINES + 49


def test_record_event_swallows_unserialisable(monkeypatch):
    # default=str keeps it best-effort rather than raising into a launch.
    fb.record_event("launcher_stage", {"obj": object()})
    assert fb.EVENTS_FILE.is_file()


# ── reader (drainer) ──────────────────────────────────────────────

def test_drain_primes_then_records_only_new():
    drainer = fb.LauncherEventDrainer()
    replay = _FakeReplay()

    # Backlog written before the UI started polling.
    fb.launcher_toast("toasts.launcher.startingGogGame")
    assert drainer.drain(replay) == 0  # prime only — no stale replay
    assert replay.recorded == []

    # A genuinely new event after priming is recorded once.
    import time
    time.sleep(0.01)
    fb.launcher_toast("toasts.launcher.protonSwitchedTo")
    assert drainer.drain(replay) == 1
    assert replay.recorded[-1][0] == "launcher_stage"
    assert replay.recorded[-1][1]["i18n_key"] == "toasts.launcher.protonSwitchedTo"

    # Idempotent — re-draining records nothing new.
    assert drainer.drain(replay) == 0
    assert len(replay.recorded) == 1


def test_drain_missing_file_is_noop():
    drainer = fb.LauncherEventDrainer()
    replay = _FakeReplay()
    assert drainer.drain(replay) == 0  # primes to 0
    assert drainer.drain(replay) == 0
    assert replay.recorded == []


def test_drain_skips_malformed_lines():
    fb.EVENTS_FILE.write_text('not json\n{"event":"launcher_stage"}\n')
    drainer = fb.LauncherEventDrainer()
    replay = _FakeReplay()
    # First entry has no ts → filtered; nothing valid to prime/record.
    assert drainer.drain(replay) == 0
    assert replay.recorded == []
