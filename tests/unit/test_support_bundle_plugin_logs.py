"""Sibling Decky plugin logs in the bundle.

Written because "CSS Loader themes didn't work" arrived with a bundle that
listed SDH-CssLoader's *version* and not one byte of its log — and that log is
where the answer is (``Committing css transaction on … +22 -0`` versus
``Cannot connect to host 127.0.0.1:8080``). The report was unanswerable
without a second round-trip to the reporter.

The rules worth pinning are about restraint, since these are other people's
files: one log per plugin (newest), tail-capped, our own excluded, and no
crash when a neighbour's directory is unreadable or empty.

``HOME`` is redirected by the fixture, so nothing here reads the developer's
real ``~/homebrew/logs``.
"""
from __future__ import annotations

import pytest

from unifideck.services.support_bundle import probe_plugin_logs as ppl


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """A fake ``~/homebrew/logs`` tree."""
    home = tmp_path / "home"
    root = home / "homebrew" / "logs"
    root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return root


def write_log(root, plugin: str, name: str, body: str, mtime: float | None = None):
    """One log file for *plugin*, optionally with a forced mtime."""
    directory = root / plugin
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    if mtime is not None:
        import os
        os.utime(path, (mtime, mtime))
    return path


def test_collects_the_newest_log_per_plugin(logs):
    write_log(logs, "SDH-CssLoader", "old.log", "STALE MARKER", mtime=1000)
    write_log(logs, "SDH-CssLoader", "new.log", "FRESH MARKER", mtime=2000)

    text, summary = ppl.render_sibling_logs()

    assert "FRESH MARKER" in text
    assert "STALE MARKER" not in text
    assert summary == [
        {"plugin": "SDH-CssLoader", "log": "new.log", "collected": True,
         "bytes": len("FRESH MARKER")},
    ]


def test_every_plugin_gets_a_slot_regardless_of_chattiness(logs):
    """A chatty neighbour must not crowd out a quiet one.

    This is why the implementation is per-directory rather than one
    ``*/*.log`` glob with a newest-N cap: under that scheme CSS Loader (which
    logs on every CEF tab change) would have consumed every slot.
    """
    for i in range(4):
        write_log(logs, "SDH-CssLoader", f"chatty{i}.log", f"CHATTY{i}", mtime=9000 + i)
    write_log(logs, "protondb-decky", "quiet.log", "QUIET MARKER", mtime=10)

    text, summary = ppl.render_sibling_logs()

    assert "QUIET MARKER" in text
    assert {row["plugin"] for row in summary} == {"SDH-CssLoader", "protondb-decky"}
    # …and only the newest from the chatty one.
    assert "CHATTY3" in text
    assert "CHATTY0" not in text


def test_our_own_logs_are_not_duplicated(logs):
    """``decky_session_logs`` already collects Unifideck's logs in full."""
    write_log(logs, "Unifideck", "ours.log", "OUR OWN LINES")
    write_log(logs, "SDH-CssLoader", "theirs.log", "THEIR LINES")

    text, summary = ppl.render_sibling_logs()

    assert "OUR OWN LINES" not in text
    assert [row["plugin"] for row in summary] == ["SDH-CssLoader"]


def test_tail_is_capped_and_newline_aligned(logs, monkeypatch):
    """A half line at the top reads as corruption; align to a newline."""
    monkeypatch.setattr(ppl, "CAP_PER_PLUGIN", 200)
    body = "".join(f"line {i:04d} padding padding padding\n" for i in range(200))
    write_log(logs, "decky-syncthing", "big.log", body)

    text, summary = ppl.render_sibling_logs()

    assert "line 0199" in text          # the tail is what we kept
    assert "line 0000" not in text
    # No partial first line: every kept log line is whole.
    for line in text.splitlines():
        if line.startswith("line "):
            assert line.endswith("padding")
    assert summary[0]["bytes"] <= 400


def test_a_log_with_no_newline_still_yields_its_content(logs, monkeypatch):
    """Newline alignment is best-effort, not a content-eater.

    A single-line log (or one line longer than the cap) used to align down to
    zero bytes, collecting a banner that said "kept 0" and nothing else.
    """
    monkeypatch.setattr(ppl, "CAP_PER_PLUGIN", 60)
    write_log(logs, "decky-lsfg-vk", "one-line.log", "X" * 100 + "NEEDLE")

    text, summary = ppl.render_sibling_logs()

    assert "NEEDLE" in text
    assert summary[0]["bytes"] > 0


def test_plugin_with_no_log_is_recorded_as_such(logs):
    """"Installed but never logged" is a fact worth reporting, not a gap."""
    (logs / "LetMeReShade").mkdir()

    _, summary = ppl.render_sibling_logs()

    assert summary == [{"plugin": "LetMeReShade", "log": None}]


def test_missing_logs_root_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    text, summary = ppl.render_sibling_logs()
    assert summary == []
    assert "no other plugin log directories found" in text


def test_total_cap_stops_collecting_and_says_so(logs, monkeypatch):
    """A silent truncation makes a bundle look complete when it is not."""
    monkeypatch.setattr(ppl, "CAP_TOTAL", 50)
    monkeypatch.setattr(ppl, "CAP_PER_PLUGIN", 50)
    write_log(logs, "aaa-plugin", "a.log", "A" * 40 + "\n" + "A" * 200)
    write_log(logs, "zzz-plugin", "z.log", "Z" * 40 + "\n" + "Z" * 200)

    _, summary = ppl.render_sibling_logs()

    dropped = [row for row in summary if row.get("collected") is False]
    assert len(dropped) == 1
    assert dropped[0]["reason"] == "artifact byte cap reached"


def test_block_reports_without_reading_bodies(logs):
    """The env block is built before collection; it must stay stat-only."""
    write_log(logs, "SDH-CssLoader", "theirs.log", "BODY BYTES")

    block = ppl.plugin_logs_block()

    assert block["root_exists"] is True
    assert block["artifact"] == "plugins/other-plugin-logs.txt"
    assert block["plugins"] == [
        {"plugin": "SDH-CssLoader", "log": "theirs.log", "bytes": len("BODY BYTES")},
    ]


def test_files_that_are_not_logs_are_ignored(logs):
    write_log(logs, "SDH-CssLoader", "theirs.log", "LOG BODY")
    (logs / "SDH-CssLoader" / "settings.json").write_text("{}", encoding="utf-8")

    text, _ = ppl.render_sibling_logs()

    assert "LOG BODY" in text
    assert "settings.json" not in text
