"""Global test isolation: the toast bridge, and $HOME-redirecting env vars.

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

The second fixture closes a different leak, one that only shows up on
CI. 18 test files build a fake device tree under a scratch dir and point
``HOME`` at it with ``monkeypatch.setenv``. That is not sufficient on its
own: production path resolution deliberately prefers the XDG variables
over ``$HOME`` (``config/user_config_path.resolve_user_config_path``
checks ``UNIFIDECK_USER_CONFIG``, then ``XDG_CONFIG_HOME``, and only then
``~/.config``), so with any of them exported the code under test walks
right past the fixture's fake home to the real one.

GitHub's ubuntu runner image exports ``XDG_CONFIG_HOME``; SteamOS and
the containers we reproduce CI in do not. So the suite passed everywhere
locally and failed only on CI, where
``test_credentials_are_still_audited_as_present`` asserted a token file
it had just written was ``present`` and got ``missing`` — the audit had
resolved ``/home/runner/.config/unifideck/`` instead of the fake home.
Two test files had already worked around this one ``delenv`` at a time;
doing it here covers every current and future HOME-patching test.

Only the variables that *redirect path resolution* are cleared.
``XDG_RUNTIME_DIR``/``XDG_SESSION_TYPE``/``XDG_CURRENT_DESKTOP`` are
left alone: the support bundle only reports those as diagnostics, and a
test asserting on the environment report should see the real values.
"""
from __future__ import annotations

import pytest

# Exported by a real desktop session or a CI runner, and honoured ahead of
# ``$HOME`` by the resolvers named in the module docstring.
_HOME_REDIRECTING_ENV = (
    "UNIFIDECK_USER_CONFIG",   # config/user_config_path (absolute override)
    "UNIFIDECK_PLUGIN_DIR",    # core/paths
    "XDG_CONFIG_HOME",         # config/user_config_path, support_bundle
    "XDG_DATA_HOME",           # stores/ubisoft/binaries
    "XDG_CACHE_HOME",          # support_bundle/probe_stack
)


@pytest.fixture(autouse=True)
def _isolate_home_redirecting_env(monkeypatch, tmp_path_factory):
    """Point ``HOME`` at a tmp dir and unset the vars that would override it.

    Unsetting alone only helped tests that remembered to patch ``HOME``
    themselves. One that forgot reached the developer's real home: a unit
    test for the nile credential self-heal ran the genuine quarantine helper
    and renamed the live ``~/.config/nile/user.json``, signing the machine
    out of Amazon. Test runs must never be able to touch real user state, so
    the redirect is applied to every test rather than opted into.

    A test that genuinely wants a different ``HOME`` (or one of these vars)
    can still ``setenv`` it in its own body: this fixture runs first, so the
    test's value wins.
    """
    for name in _HOME_REDIRECTING_ENV:
        monkeypatch.delenv(name, raising=False)
    # A dir of its own, NOT under the test's ``tmp_path``: several tests
    # assert on the exact contents of ``tmp_path`` and a stray home sandbox
    # inside it makes them fail.
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    # ``Path.home()`` consults these before ``HOME`` on some platforms, and
    # ``os.path.expanduser`` falls back to the password database when HOME is
    # absent — keep both pointing at the sandbox.
    monkeypatch.setenv("USERPROFILE", str(home))


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
