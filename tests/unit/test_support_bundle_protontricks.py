"""The Protontricks readiness probe and its verdict.

Written because a "Protontricks still not working, giving errors" report
arrived with a 13 MB support bundle in which the word "protontricks" appeared
**zero times**. The only line the plugin logged was
``[prefix_bridge] … flatpak=absent``, which conflates "no flatpak" with "no
Protontricks Flatpak" — so a native install in active use looked like nothing
was installed at all.

These cases pin the two things that make the next such report answerable:

* the distribution is reported unambiguously (native / flatpak / absent), and
  a ``pip install --user`` binary outside a root ``PATH`` is still found;
* the verdict names *which* of Protontricks' three preconditions failed —
  prefix gates, Proton discovery, or its own reported error — rather than
  saying something vague.

Nothing here shells out: ``run_demoted`` is stubbed throughout, and every
module constant that points into the real data dir is redirected.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from unifideck.core import compat_bridge, compat_tool_bridge
from unifideck.services.support_bundle import (
    checks_protontricks,
    probe_protontricks,
)
from unifideck.services.support_bundle.check_kit import View

FLATPAK_REF = "app/com.github.Matoking.protontricks/x86_64/stable"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect every real-data-dir constant the probe reads."""
    prefixes = tmp_path / "prefixes"
    tools = tmp_path / "protontricks-tools"
    steam_root = tmp_path / "steam"
    prefixes.mkdir()
    (steam_root / "steamapps" / "compatdata").mkdir(parents=True)

    monkeypatch.setattr(compat_bridge, "PREFIX_ROOT", prefixes)
    monkeypatch.setattr(compat_tool_bridge, "BRIDGE_ROOT", tools)
    monkeypatch.setattr(
        probe_protontricks.vdf_compat, "resolve_live_steam_root",
        lambda *a, **k: steam_root,
    )
    # No Protontricks and no flatpak unless a test says otherwise.
    monkeypatch.setattr(probe_protontricks.shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        probe_protontricks, "run_demoted", lambda *a, **k: None,
    )
    return prefixes, tools, steam_root


def stub_runs(monkeypatch, responses):
    """Stub ``run_demoted``, dispatching on a substring of the joined argv."""
    def _run(argv, uid, gid=None, *, timeout=None):
        joined = " ".join(argv)
        for needle, result in responses.items():
            if needle in joined:
                return result
        return None
    monkeypatch.setattr(probe_protontricks, "run_demoted", _run)


def ok(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout, "")


def fail(stderr="boom"):
    return subprocess.CompletedProcess([], 1, "", stderr)


def make_bridge(steam_root, prefixes, appid: str, *, healthy: bool = True):
    """A compatdata bridge, optionally missing Protontricks' gates."""
    prefix = prefixes / f"game-{appid}"
    prefix.mkdir(parents=True)
    if healthy:
        (prefix / "pfx").symlink_to(".", target_is_directory=True)
        (prefix / "pfx.lock").touch()
    link = steam_root / "steamapps" / "compatdata" / appid
    link.symlink_to(prefix, target_is_directory=True)
    return prefix


# --------------------------------------------------------------------------
# distribution — the ambiguity the probe exists to remove
# --------------------------------------------------------------------------
def test_absent_when_neither_distribution_is_installed(env):
    block = probe_protontricks.protontricks_block()
    assert block["distribution"] == {"primary": "absent", "installed": []}
    # And it must not burn the listing timeout asking a binary that isn't there.
    assert block["listing"] == {"skipped": "no Protontricks installed"}


def test_native_install_is_reported_as_native(env, monkeypatch):
    monkeypatch.setattr(
        probe_protontricks.shutil, "which", lambda _n: "/usr/bin/protontricks",
    )
    stub_runs(monkeypatch, {"--version": ok("protontricks 1.14.1"), "-l": ok("")})

    block = probe_protontricks.protontricks_block()

    assert block["distribution"]["primary"] == "native"
    installed = block["distribution"]["installed"][0]
    assert installed["path"] == "/usr/bin/protontricks"
    assert installed["version"] == "protontricks 1.14.1"


def test_pip_user_install_outside_root_path_is_still_found(
    env, monkeypatch, tmp_path,
):
    """The backend can run as root with a minimal PATH; ~/.local/bin is not on it.

    Missing this reported ``absent`` for a Protontricks the user runs daily.
    """
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    binary = home / ".local" / "bin" / "protontricks"
    binary.touch()
    monkeypatch.setenv("HOME", str(home))
    stub_runs(monkeypatch, {"--version": ok("protontricks 1.12.0"), "-l": ok("")})

    block = probe_protontricks.protontricks_block()

    assert block["distribution"]["primary"] == "native"
    assert block["distribution"]["installed"][0]["path"] == str(binary)


def test_flatpak_only_install_is_reported_as_flatpak(env, monkeypatch):
    stub_runs(monkeypatch, {"info": ok(FLATPAK_REF), "-l": ok("")})

    block = probe_protontricks.protontricks_block()

    assert block["distribution"]["primary"] == "flatpak"
    assert block["distribution"]["installed"][0]["ref"] == FLATPAK_REF
    assert "flatpak run" in block["listing"]["argv"]


# --------------------------------------------------------------------------
# prefix bridges — Protontricks' two gates
# --------------------------------------------------------------------------
def test_healthy_bridge_reports_both_gates_satisfied(env):
    prefixes, _, steam_root = env
    make_bridge(steam_root, prefixes, "3013071580")

    rows = probe_protontricks.protontricks_block()["prefix_bridge"]["bridges"]

    assert len(rows) == 1
    assert rows[0]["appid"] == "3013071580"
    assert rows[0]["target_exists"] is True
    assert rows[0]["pfx_is_dir"] is True
    assert rows[0]["pfx_lock_is_file"] is True


def test_bridge_without_pfx_is_flagged(env):
    """A link that looks fine in `ls` but Protontricks silently skips."""
    prefixes, _, steam_root = env
    make_bridge(steam_root, prefixes, "3013071580", healthy=False)

    rows = probe_protontricks.protontricks_block()["prefix_bridge"]["bridges"]

    assert rows[0]["pfx_is_dir"] is False
    assert rows[0]["pfx_lock_is_file"] is False


def test_foreign_compatdata_entries_are_not_reported_as_ours(env):
    """The user's own prefixes are none of our business to describe."""
    prefixes, _, steam_root = env
    compatdata = steam_root / "steamapps" / "compatdata"
    (compatdata / "1091500").mkdir()          # a real Steam prefix
    elsewhere = steam_root / "not-ours"
    elsewhere.mkdir()
    (compatdata / "999").symlink_to(elsewhere)  # someone else's link

    assert probe_protontricks.protontricks_block()["prefix_bridge"]["bridges"] == []


def test_missing_steam_root_degrades_quietly(env, monkeypatch):
    monkeypatch.setattr(
        probe_protontricks.vdf_compat, "resolve_live_steam_root",
        lambda *a, **k: None,
    )
    assert probe_protontricks.protontricks_block()["prefix_bridge"]["bridges"] == []


# --------------------------------------------------------------------------
# verdict
# --------------------------------------------------------------------------
def _verdict(block: dict[str, Any]):
    view = View.__new__(View)
    view.env = {"protontricks": block} if block is not None else {}
    view.by_key = {}
    return checks_protontricks.check_protontricks(view)


def test_verdict_is_na_without_protontricks():
    assert _verdict({"distribution": {"primary": "absent"}}).status == "na"
    assert _verdict({}).status == "na"


def test_verdict_passes_and_names_what_it_can_see():
    verdict = _verdict({
        "distribution": {"primary": "flatpak"},
        "prefix_bridge": {"bridges": [
            {"appid": "1", "pfx_is_dir": True, "pfx_lock_is_file": True},
        ]},
        "compat_tool_bridge": {"links": [{"link": "x"}], "sandbox_access": "already"},
        "listing": {"stderr": ""},
    })
    assert verdict.status == "pass"
    assert "flatpak" in verdict.detail
    assert "1 prefix bridge(s)" in verdict.detail


def test_verdict_warns_on_a_bridge_failing_the_gates():
    verdict = _verdict({
        "distribution": {"primary": "native"},
        "prefix_bridge": {"bridges": [
            {"appid": "3013071580", "pfx_is_dir": False, "pfx_lock_is_file": True},
        ]},
        "compat_tool_bridge": {},
        "listing": {},
    })
    assert verdict.status == "warn"
    assert "3013071580" in verdict.detail
    assert "skip those games" in verdict.detail


def test_verdict_warns_when_the_sandbox_cannot_search_the_tool_links():
    """Readable but never searched — configured-looking silent failure."""
    verdict = _verdict({
        "distribution": {"primary": "flatpak"},
        "prefix_bridge": {"bridges": []},
        "compat_tool_bridge": {
            "links": [{"link": "unifideck-bridge-proton-cachyos"}],
            "sandbox_access": "partial",
        },
        "listing": {},
    })
    assert verdict.status == "warn"
    assert "sandbox_access=partial" in verdict.detail


def test_verdict_quotes_protontricks_own_error():
    """Its words, not our paraphrase — that is the line a reporter never sent."""
    verdict = _verdict({
        "distribution": {"primary": "flatpak"},
        "prefix_bridge": {"bridges": []},
        "compat_tool_bridge": {},
        "listing": {"stderr": (
            "protontricks (WARNING): Steam library folder /x does not exist\n"
            "protontricks (ERROR): Active Proton installation could not be "
            "found automatically."
        )},
    })
    assert verdict.status == "warn"
    assert "Active Proton installation could not be found" in verdict.detail
    # The unrelated library warning must not be what gets quoted.
    assert "does not exist" not in verdict.detail


def test_verdict_with_no_tool_links_does_not_blame_the_sandbox():
    """Nothing bridged means nothing needed bridging — not a problem."""
    verdict = _verdict({
        "distribution": {"primary": "native"},
        "prefix_bridge": {"bridges": []},
        "compat_tool_bridge": {"links": [], "sandbox_access": "absent"},
        "listing": {"stderr": ""},
    })
    assert verdict.status == "pass"


# --------------------------------------------------------------------------
# The probe must not mutate what it describes
# --------------------------------------------------------------------------
def test_probe_creates_nothing(env):
    prefixes, tools, steam_root = env
    before = {
        str(p) for root in (prefixes, steam_root) for p in Path(root).rglob("*")
    }

    probe_protontricks.protontricks_block()

    after = {
        str(p) for root in (prefixes, steam_root) for p in Path(root).rglob("*")
    }
    assert before == after
    assert not tools.exists()
