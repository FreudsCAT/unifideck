"""Tests for utils/vdf_compat — Steam root/config.vdf + compat-tool discovery.

These lock in the cross-distro Proton-resolution fixes (Bazzite/CachyOS):

* ``config.vdf`` ``CompatToolMapping`` parsing — per-app and the ``"0"``
  global default that distros ship pre-set.
* compat-tool enumeration that follows ``compatibilitytool.vdf`` manifests
  (per-dir and loose ``.vdf``) so a tool whose ``install_path`` points at
  the system-wide ``/usr/share/steam/compatibilitytools.d`` (how CachyOS's
  ``proton-cachyos`` registers) resolves — a bare directory scan misses it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from unifideck.utils import vdf_compat as vc


def _write_manifest(
    path: Path, internal: str, install_path: str, display: str | None = None,
) -> None:
    """Write a realistic (Valve-style, expanded) ``compatibilitytool.vdf``.

    The vendored ``vdf`` KeyValues parser flattens ``"a" { "b" {`` when the
    braces sit inline on one line; real tool manifests (GE, proton-cachyos)
    always put each brace on its own line, so tests must too.
    """
    display_line = f'\t\t\t"display_name"\t"{display}"\n' if display else ""
    path.write_text(
        '"compatibilitytools"\n{\n\t"compat_tools"\n\t{\n'
        f'\t\t"{internal}"\n\t\t{{\n'
        f'\t\t\t"install_path"\t"{install_path}"\n'
        f"{display_line}"
        '\t\t}\n\t}\n}\n',
    )

_CONFIG_VDF = """
"InstallConfigStore"
{
  "Software" { "Valve" { "Steam"
  {
    "CompatToolMapping"
    {
      "0"   { "name" "proton-cachyos" "config" "" "priority" "75" }
      "480" { "name" "GE-Proton8-30"  "config" "" "priority" "250" }
    }
  } } }
}
"""


def _proton(dir_path: Path) -> Path:
    """Create ``<dir_path>/proton`` and return it."""
    dir_path.mkdir(parents=True, exist_ok=True)
    proton = dir_path / "proton"
    proton.write_text("#!/bin/sh\n")
    return proton


# ── config.vdf parsing ────────────────────────────────────────────

def test_parse_global_default_reads_mapping_zero() -> None:
    assert vc.parse_global_default_compat_tool(_CONFIG_VDF) == "proton-cachyos"


def test_parse_compat_tool_reads_per_app() -> None:
    assert vc.parse_compat_tool(_CONFIG_VDF, 480) == "GE-Proton8-30"


def test_parse_compat_tool_absent_appid_is_empty() -> None:
    assert vc.parse_compat_tool(_CONFIG_VDF, 999) == ""


def test_parse_helpers_empty_content() -> None:
    assert vc.parse_global_default_compat_tool("") == ""
    assert vc.parse_compat_tool("", 480) == ""


def test_global_default_not_falsematched_outside_mapping() -> None:
    """A ``"0" { … }`` outside CompatToolMapping must not be picked up."""
    content = '"Other" { "0" { "name" "nope" } }'
    assert vc.parse_global_default_compat_tool(content) == ""


# ── compat-tool enumeration + resolution ──────────────────────────

def test_loose_vdf_manifest_resolves_install_path(tmp_path: Path) -> None:
    """CachyOS style: a loose ``.vdf`` in the user dir points at a system dir."""
    sysdir = tmp_path / "usr_share_steam" / "proton-cachyos"
    proton = _proton(sysdir)
    userdir = tmp_path / "user_compat"
    userdir.mkdir()
    _write_manifest(
        userdir / "proton-cachyos.vdf", "proton-cachyos", str(sysdir),
        "Proton-CachyOS",
    )
    tools = vc.iter_compat_tools([userdir])
    assert tools.get("proton-cachyos") == proton
    assert tools.get("Proton-CachyOS") == proton  # display name too


def test_per_dir_manifest_with_relative_install_path(tmp_path: Path) -> None:
    # install_path points at a SIBLING dir (not "."), so only the manifest —
    # not the bare-dir fallback — can resolve it.
    tool = tmp_path / "tool-home"
    (tool / "compatibilitytool.vdf").parent.mkdir(parents=True)
    payload = _proton(tmp_path / "tool-home" / "payload")
    _write_manifest(
        tool / "compatibilitytool.vdf", "GE-Proton9-1", "payload", "GE-Proton9-1",
    )
    assert vc.resolve_compat_tool("GE-Proton9-1", [tmp_path]) == payload


def test_bare_directory_without_manifest_resolves(tmp_path: Path) -> None:
    proton = _proton(tmp_path / "GE-Proton10-10")
    assert vc.resolve_compat_tool("GE-Proton10-10", [tmp_path]) == proton


def test_resolve_is_case_insensitive_fallback(tmp_path: Path) -> None:
    proton = _proton(tmp_path / "GE-Proton10-10")
    assert vc.resolve_compat_tool("ge-proton10-10", [tmp_path]) == proton


def test_resolve_unknown_tool_is_none(tmp_path: Path) -> None:
    _proton(tmp_path / "GE-Proton10-10")
    assert vc.resolve_compat_tool("Proton-DoesNotExist", [tmp_path]) is None


def test_iter_skips_missing_proton_binary(tmp_path: Path) -> None:
    """A manifest whose install_path has no ``proton`` script is dropped."""
    tool = tmp_path / "Broken"
    tool.mkdir()
    _write_manifest(tool / "compatibilitytool.vdf", "Broken", "payload")
    assert vc.iter_compat_tools([tmp_path]) == {}


def test_earlier_root_wins_on_name_collision(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    p1 = _proton(first / "GE-Proton10-10")
    _proton(second / "GE-Proton10-10")
    assert vc.resolve_compat_tool("GE-Proton10-10", [first, second]) == p1


# ── Steam root / config.vdf discovery ─────────────────────────────

def test_find_steam_root_and_config_vdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    steam = home / ".local" / "share" / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    assert vc.find_steam_root() == steam
    assert vc.find_steam_config_vdf() is None  # no config.vdf yet

    (steam / "config").mkdir()
    (steam / "config" / "config.vdf").write_text(_CONFIG_VDF)
    assert vc.find_steam_config_vdf() == steam / "config" / "config.vdf"


def test_find_steam_root_none_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "empty"))
    assert vc.find_steam_root() is None
    assert vc.find_steam_config_vdf() is None


def test_resolve_live_steam_root_dedupes_symlinked_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The standard layout — ``~/.steam/steam`` → ``~/.local/share/Steam`` — is
    ONE install, not two competing ones. Dedup keeps behaviour unchanged on a
    normal single-Steam machine (no false 'ambiguous installs')."""
    home = tmp_path / "home"
    real = home / ".local" / "share" / "Steam"
    (real / "steamapps").mkdir(parents=True)
    (home / ".steam").mkdir(parents=True)
    (home / ".steam" / "steam").symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))

    resolved = vc.resolve_live_steam_root()
    assert resolved is not None
    assert resolved.resolve() == real.resolve()


def test_resolve_live_steam_root_picks_freshest_of_distinct_installs(
    tmp_path: Path,
) -> None:
    """Two genuinely distinct installs → the more recently active one wins."""
    import os

    def _mk(sub: str, ts: int) -> Path:
        root = tmp_path / sub
        (root / "steamapps").mkdir(parents=True)
        login = root / "config" / "loginusers.vdf"
        login.parent.mkdir(parents=True)
        login.write_text(
            '"users"\n{\n\t"76561197960265728"\n\t{\n'
            '\t\t"MostRecent"\t\t"1"\n'
            f'\t\t"Timestamp"\t\t"{ts}"\n\t}}\n}}\n',
        )
        os.utime(login, (ts, ts))
        return root

    stale = _mk("stale", 1_000_000_000)
    live = _mk("live", 2_000_000_000)

    assert vc.resolve_live_steam_root([str(stale), str(live)]) == live
