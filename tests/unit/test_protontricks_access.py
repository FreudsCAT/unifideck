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
