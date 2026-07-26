"""Global test isolation for the launcher → frontend toast bridge.

``frontend_bridge.EVENTS_FILE`` is a module-level constant pointing at the
REAL ``~/.local/share/unifideck/launcher_events.jsonl``. Any test that
exercises a path calling ``launcher_toast`` — umu retry, compat/prereq
install, store handlers — therefore appended genuine toast events to the
live file. The plugin's *persistent* ``get_launcher_toasts`` poll drains
that file regardless of whether the QAM panel is open, so running the
suite popped real "Retrying Launch — Retrying UMU in 3s (attempt 2/2)…"
toasts into the Steam UI.

That is worse than cosmetic. The file is capped at 100 lines AND is
collected into diagnostic bundles, so a test run silently evicted real
launch history — the exact evidence a bug report depends on. Measured
before this fixture landed: 36 of 79 live lines were test noise.

``tests/unit/test_frontend_bridge.py`` already redirects ``EVENTS_FILE``
for its own cases; this does it for every other test, autouse, so no test
can reach the user's data dir. A test that patches ``EVENTS_FILE``
explicitly still wins — this only guarantees the default is never live.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_launcher_bridge(tmp_path, monkeypatch):
    """Point the launcher→frontend bridge file at a per-test temp path."""
    # Imported inside the fixture so conftest import never depends on
    # sys.path being set up yet (pytest.ini's ``pythonpath`` handles it,
    # but collection order should not be load-bearing here).
    from unifideck.launcher import frontend_bridge

    monkeypatch.setattr(
        frontend_bridge,
        "EVENTS_FILE",
        tmp_path / "launcher_events.jsonl",
    )
