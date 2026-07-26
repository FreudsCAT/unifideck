"""Regression: bundled CLI subprocesses must not inherit a poisoned env.

The Decky backend is a PyInstaller-frozen binary, so ``os.environ`` inside
the plugin carries ``LD_LIBRARY_PATH=/tmp/_MEIxxxx`` (the loader's own
bundled libs) plus PyInstaller's ``*_ORIG`` stash. Handing that to a child
that is not the frozen app makes it link the wrong libraries — the repo
already fixed this locally for curl, for umu-run (which exited 127 on
"libz.so.1" inside pressure-vessel), and in the launcher bootstrap.

The store CLIs never had that protection, and the zipapp switch made it
matter: legendary >=0.20.40 and gogdl >=1.2.2 run under the SYSTEM python3
via a shebang, so unlike the old frozen ELFs they obey both the loader
variables AND ``PYTHONHOME``/``PYTHONPATH``. With the plugin's
``py_modules`` on ``PYTHONPATH``, legendary's ``Cryptodome`` import
resolved our vendored ``cffi`` and died with ``Exception: Version
mismatch`` before parsing a single argument.
"""
from __future__ import annotations

import os

import pytest

from unifideck.core.binaries.cli_env import (
    SCRUBBED_VARS,
    clean_cli_env,
    scrub_cli_env,
)

_POISON = {
    "LD_LIBRARY_PATH": "/tmp/_MEI123456",
    "LD_LIBRARY_PATH_ORIG": "/usr/lib/steam-runtime",
    "LD_PRELOAD": "/usr/lib/gameoverlayrenderer.so",
    "LD_PRELOAD_ORIG": "/usr/lib/gameoverlayrenderer.so",
    "PYTHONHOME": "/opt/decky/python",
    "PYTHONPATH": "/home/deck/homebrew/plugins/Unifideck/py_modules",
}


@pytest.fixture
def _poisoned(monkeypatch):
    for key, value in _POISON.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOME", "/home/deck")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")


def test_every_poison_var_is_removed(_poisoned):
    env = clean_cli_env()
    leaked = sorted(k for k in _POISON if k in env)
    assert not leaked, f"these would reach the CLI: {leaked}"


def test_path_and_home_survive(_poisoned):
    """The shebang needs PATH to find python3; the cache dir needs HOME.

    Scrubbing either would break the zipapps outright, so this is the
    guard against over-scrubbing.
    """
    env = clean_cli_env()
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/deck"


def test_unrelated_vars_are_preserved(_poisoned, monkeypatch):
    monkeypatch.setenv("GOGDL_CONFIG_PATH", "/home/deck/.config/unifideck")
    monkeypatch.setenv("XDG_CACHE_HOME", "/home/deck/.cache")

    env = clean_cli_env()

    assert env["GOGDL_CONFIG_PATH"] == "/home/deck/.config/unifideck"
    assert env["XDG_CACHE_HOME"] == "/home/deck/.cache"


def test_os_environ_is_never_mutated(_poisoned):
    """The backend is long-lived — scrubbing must not leak into the process."""
    clean_cli_env()
    assert os.environ["LD_LIBRARY_PATH"] == "/tmp/_MEI123456"
    assert os.environ["PYTHONPATH"].endswith("py_modules")


def test_overrides_are_applied_after_scrubbing(_poisoned):
    """A caller can still set a deliberate value for a scrubbed var."""
    env = clean_cli_env({"PYTHONPATH": "/deliberate", "FOO": "bar"})
    assert env["PYTHONPATH"] == "/deliberate"
    assert env["FOO"] == "bar"


def test_overrides_do_not_resurrect_poison_by_accident(_poisoned):
    env = clean_cli_env({"FOO": "bar"})
    assert "PYTHONPATH" not in env
    assert "LD_LIBRARY_PATH" not in env


def test_scrub_cli_env_cleans_a_caller_built_dict():
    """For call sites that assemble their own env instead of starting clean."""
    env = {**_POISON, "GOGDL_CONFIG_PATH": "/x", "PATH": "/usr/bin"}

    result = scrub_cli_env(env)

    assert all(k not in result for k in _POISON)
    assert result["GOGDL_CONFIG_PATH"] == "/x"
    assert result["PATH"] == "/usr/bin"


def test_scrub_cli_env_tolerates_an_already_clean_dict():
    env = {"PATH": "/usr/bin"}
    assert scrub_cli_env(env) == {"PATH": "/usr/bin"}


def test_orig_twins_are_dropped_not_restored(_poisoned):
    """PyInstaller stashes the pre-freeze value; it is no more welcome.

    Restoring from ``*_ORIG`` hands back Steam's own loader paths, which is
    exactly the bug that made every GOG/Amazon/Ubisoft launch exit 127.
    """
    env = clean_cli_env()
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert "LD_PRELOAD_ORIG" not in env
    assert env.get("LD_LIBRARY_PATH") is None


def test_scrubbed_vars_is_the_documented_set():
    assert set(SCRUBBED_VARS) == set(_POISON)
