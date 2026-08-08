"""Flatpak permission grant for Protontricks.

The bridge symlink alone is not enough for the Flatpak build: its sandbox
has no access to ``~/.local/share/unifideck``, so the link dangles in-sandbox
and Protontricks skips the shortcut exactly as if no bridge existed. These
cases pin the detection/grant state machine and — importantly — that the
grant is narrow (the prefixes dir, not the whole data dir, which holds auth
tokens).
"""
from __future__ import annotations

import subprocess

import pytest

from unifideck.services import protontricks_access as pa


@pytest.fixture
def prefixes(tmp_path):
    p = tmp_path / "prefixes"
    p.mkdir()
    return p


def fake_run(responses, calls):
    """Stub ``run_demoted``: dispatch on a substring of the argv."""
    def _run(argv, uid, gid=None, *, timeout=None):
        calls.append(argv)
        for needle, result in responses.items():
            if needle in argv:
                return result
        return None
    return _run


def ok(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout, "")


def fail(stderr="boom"):
    return subprocess.CompletedProcess([], 1, "", stderr)


GRANTED = "[Context]\nfilesystems=/home/deck/Documents;{path};\n"


def test_grants_when_flatpak_present_without_access(prefixes, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(pa, "run_demoted", fake_run(
        {
            "info": ok(),
            "--show": ok("[Context]\nfilesystems=~/.steam;\n"),
            f"--filesystem={prefixes}": ok(),
        },
        calls,
    ))

    assert pa.ensure_access(prefixes) == "granted"
    override = next((c for c in calls if f"--filesystem={prefixes}" in c), None)
    assert override is not None, "expected a flatpak override call"
    # Narrow grant: exactly the prefixes dir — never the parent data dir,
    # which holds auth tokens and caches.
    granted = [a for a in override if a.startswith("--filesystem=")]
    assert granted == [f"--filesystem={prefixes}"]
    assert "--user" in override
    assert pa.FLATPAK_APP_ID in override


def test_already_granted_is_a_noop(prefixes, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(pa, "run_demoted", fake_run(
        {"info": ok(), "--show": ok(GRANTED.format(path=prefixes))}, calls,
    ))

    assert pa.ensure_access(prefixes) == "already"
    assert not [c for c in calls if f"--filesystem={prefixes}" in c]


def test_ancestor_grant_counts_as_access(prefixes, monkeypatch):
    """A user who exposed their whole home is already covered."""
    calls: list[list[str]] = []
    monkeypatch.setattr(pa, "run_demoted", fake_run(
        {"info": ok(), "--show": ok(GRANTED.format(path=prefixes.parent.parent))},
        calls,
    ))
    assert pa.ensure_access(prefixes) == "already"


def test_absent_flatpak_reports_absent(prefixes, monkeypatch):
    monkeypatch.setattr(pa, "run_demoted", fake_run({"info": fail()}, []))
    assert pa.ensure_access(prefixes) == "absent"


def test_missing_prefixes_dir_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "run_demoted", fake_run({}, []))
    assert pa.ensure_access(tmp_path / "does-not-exist") == "skipped"


def test_override_failure_is_reported_not_raised(prefixes, monkeypatch):
    monkeypatch.setattr(pa, "run_demoted", fake_run(
        {
            "info": ok(),
            "--show": ok("[Context]\nfilesystems=~/.steam;\n"),
            f"--filesystem={prefixes}": fail(),
        },
        [],
    ))
    assert pa.ensure_access(prefixes) == "failed"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[Context]\nfilesystems=/a;/b;\n", ["/a", "/b"]),
        ("[Context]\nfilesystems=~/.steam:ro;/tmp:create;\n", ["~/.steam", "/tmp"]),
        # Placeholder tokens are not paths and must be dropped.
        ("[Context]\nfilesystems=host;home;xdg-music;\n", []),
        ("[Context]\nsockets=x11;\n", []),
        ("", []),
    ],
)
def test_granted_path_parsing(raw, expected):
    assert pa._granted_paths(raw) == expected


# --------------------------------------------------------------------------
# The compat-tool path grant
#
# A readable directory Protontricks never LOOKS IN is worth nothing, so this
# grant is two things at once: --filesystem (read it) and the
# STEAM_EXTRA_COMPAT_TOOLS_PATHS env entry (search it). Both must be asserted
# together — that pairing is the whole point of the fix.
# --------------------------------------------------------------------------
@pytest.fixture
def tools(tmp_path):
    t = tmp_path / "protontricks-tools"
    t.mkdir()
    return t


def test_tool_grant_adds_both_filesystem_and_search_path(tools, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(pa, "run_demoted", fake_run(
        {
            "info": ok(),
            "--show": ok("[Context]\nfilesystems=~/.steam;\n"),
            f"--filesystem={tools}:ro": ok(),
        },
        calls,
    ))

    assert pa.ensure_tool_path_access(tools) == "granted"
    override = next((c for c in calls if any("--env=" in a for a in c)), None)
    assert override is not None, "expected an override call carrying --env"
    assert f"--filesystem={tools}:ro" in override
    assert f"--env={pa.EXTRA_TOOLS_ENV}={tools}" in override


def test_tool_grant_appends_to_an_existing_search_path(tools, monkeypatch):
    """A value the user (or another tool) set must never be clobbered."""
    calls: list[list[str]] = []
    monkeypatch.setattr(pa, "run_demoted", fake_run(
        {
            "info": ok(),
            "--show": ok(
                f"[Context]\nfilesystems={tools};\n\n"
                f"[Environment]\n{pa.EXTRA_TOOLS_ENV}=/opt/theirs\n",
            ),
            f"--filesystem={tools}:ro": ok(),
        },
        calls,
    ))

    assert pa.ensure_tool_path_access(tools) == "granted"
    override = next(c for c in calls if any("--env=" in a for a in c))
    env_arg = next(a for a in override if a.startswith("--env="))
    assert env_arg == f"--env={pa.EXTRA_TOOLS_ENV}=/opt/theirs:{tools}"


def test_tool_grant_is_idempotent(tools, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(pa, "run_demoted", fake_run(
        {
            "info": ok(),
            "--show": ok(
                f"[Context]\nfilesystems={tools}:ro;\n\n"
                f"[Environment]\n{pa.EXTRA_TOOLS_ENV}={tools}\n",
            ),
        },
        calls,
    ))

    assert pa.ensure_tool_path_access(tools) == "already"
    assert not [c for c in calls if any("--env=" in a for a in c)]


def test_tool_grant_repairs_a_half_configured_override(tools, monkeypatch):
    """Filesystem granted but never searched — the silent-failure shape."""
    calls: list[list[str]] = []
    monkeypatch.setattr(pa, "run_demoted", fake_run(
        {
            "info": ok(),
            "--show": ok(f"[Context]\nfilesystems={tools}:ro;\n"),
            f"--filesystem={tools}:ro": ok(),
        },
        calls,
    ))

    assert pa.tool_path_status(tools, tools) == "partial"
    assert pa.ensure_tool_path_access(tools) == "granted"


def test_tool_grant_without_flatpak_is_absent(tools, monkeypatch):
    monkeypatch.setattr(pa, "run_demoted", fake_run({"info": fail()}, []))
    assert pa.ensure_tool_path_access(tools) == "absent"


def test_tool_grant_skips_when_no_links_exist_yet(tmp_path, monkeypatch):
    """Nothing is bridged yet, so there is nothing to expose."""
    monkeypatch.setattr(pa, "run_demoted", fake_run({}, []))
    assert pa.ensure_tool_path_access(tmp_path / "nope") == "skipped"


def test_tool_grant_failure_is_reported_not_raised(tools, monkeypatch):
    monkeypatch.setattr(pa, "run_demoted", fake_run(
        {
            "info": ok(),
            "--show": ok("[Context]\nfilesystems=~/.steam;\n"),
            f"--filesystem={tools}:ro": fail(),
        },
        [],
    ))
    assert pa.ensure_tool_path_access(tools) == "failed"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[Environment]\nSTEAM_EXTRA_COMPAT_TOOLS_PATHS=/a:/b\n", ["/a", "/b"]),
        ("[Environment]\nSTEAM_EXTRA_COMPAT_TOOLS_PATHS=\n", []),
        ("[Environment]\nOTHER=/a\n", []),
        ("", []),
    ],
)
def test_env_entry_parsing(raw, expected):
    assert pa._env_entries(raw, pa.EXTRA_TOOLS_ENV) == expected
