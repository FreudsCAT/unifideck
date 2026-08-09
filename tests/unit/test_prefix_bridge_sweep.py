"""The sweep that keeps compatdata bridges in step with installed games.

Driven by ``games.map`` because that file lists exactly the *installed*
games and carries the canonical appid in its v3 column. Recomputing the appid
would be a bug: ``generate_app_id`` is anchored on the launcher exe path, so a
derived id does not match the stored one (confirmed on-device).
"""
from __future__ import annotations

import pytest

from unifideck.core import compat_bridge, compat_tool_bridge
from unifideck.services import prefix_bridge


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect the module-level data-dir constants at a scratch tree."""
    data = tmp_path / "data"
    prefixes = data / "prefixes"
    steam_root = tmp_path / "steam"
    prefixes.mkdir(parents=True)
    (steam_root / "steamapps" / "compatdata").mkdir(parents=True)

    monkeypatch.setattr(compat_bridge, "PREFIX_ROOT", prefixes)
    monkeypatch.setattr(prefix_bridge.compat_bridge, "PREFIX_ROOT", prefixes)
    monkeypatch.setattr(prefix_bridge, "_GAMES_MAP", data / "games.map")
    monkeypatch.setattr(prefix_bridge, "_UBISOFT_ID_MAP", data / "ubisoft_id_map.json")
    # The Flatpak grants are exercised in their own test module; keep this one
    # from shelling out.
    monkeypatch.setattr(
        "unifideck.services.protontricks_access.ensure_access",
        lambda *a, **k: "absent",
    )
    monkeypatch.setattr(
        "unifideck.services.protontricks_access.ensure_tool_path_access",
        lambda *a, **k: "absent",
    )
    # The compat-tool bridge root is a real data-dir path; without this a
    # developer who happens to have one would have the sweep prune THEIR
    # links (and shell out to flatpak) during a test run.
    monkeypatch.setattr(
        compat_tool_bridge, "BRIDGE_ROOT", data / "protontricks-tools",
    )
    return data, prefixes, steam_root


def write_games_map(data, rows):
    """rows: ``[(key, exe, workdir, signed_app_id)]``."""
    lines = ["# Unifideck non-Steam shortcut manifest (games.map)"]
    lines += [f"{k}={e}\t{w}\t{a}" for k, e, w, a in rows]
    (data / "games.map").write_text("\n".join(lines) + "\n")


def make_prefix(prefixes, name):
    p = prefixes / name
    p.mkdir(parents=True)
    (p / "pfx").symlink_to(".", target_is_directory=True)
    (p / "pfx.lock").touch()
    return p


def test_links_installed_games_and_is_idempotent(env):
    data, prefixes, steam_root = env
    make_prefix(prefixes, "Sugar")
    write_games_map(data, [("epic:Sugar", "/g/rl.exe", "/g", -487067706)])

    first = prefix_bridge.sync_bridges(steam_root)
    assert first["linked"] == 1

    link = steam_root / "steamapps" / "compatdata" / "3807899590"
    assert link.resolve() == (prefixes / "Sugar").resolve()

    second = prefix_bridge.sync_bridges(steam_root)
    assert second["linked"] == 0 and second["already"] == 1


def test_uninstalled_game_is_pruned_from_protontricks(env):
    """A prefix removed by an uninstall must drop its bridge on the next sweep."""
    data, prefixes, steam_root = env
    prefix = make_prefix(prefixes, "Sugar")
    write_games_map(data, [("epic:Sugar", "/g/rl.exe", "/g", -487067706)])
    prefix_bridge.sync_bridges(steam_root)

    for child in prefix.iterdir():
        child.unlink()
    prefix.rmdir()
    (data / "games.map").write_text("# empty\n")

    assert prefix_bridge.sync_bridges(steam_root)["pruned"] == 1
    assert not (steam_root / "steamapps" / "compatdata" / "3807899590").is_symlink()


def test_rows_without_a_real_appid_are_skipped(env):
    """``app_id == 0`` is the not-yet-backfilled marker, not an appid."""
    data, prefixes, steam_root = env
    make_prefix(prefixes, "Sugar")
    write_games_map(data, [("epic:Sugar", "/g/rl.exe", "/g", 0)])

    assert prefix_bridge.sync_bridges(steam_root)["linked"] == 0


def test_ubisoft_prefix_path_comes_from_the_id_map(env):
    """Ubisoft games can install anywhere; the map records the real prefix."""
    import json

    data, prefixes, steam_root = env
    custom = prefixes / "ubisoft" / "custom-location"
    custom.mkdir(parents=True)
    (data / "ubisoft_id_map.json").write_text(
        json.dumps({"720": {"prefix_path": str(custom)}}),
    )
    write_games_map(data, [("ubisoft:720", "/g/x.exe", "/g", 12345)])

    assert prefix_bridge.resolve_prefix("ubisoft", "720") == custom
    assert prefix_bridge.sync_bridges(steam_root)["linked"] == 1
    assert (steam_root / "steamapps" / "compatdata" / "12345").resolve() \
        == custom.resolve()


def test_ubisoft_falls_back_to_the_namespaced_default(env):
    _, prefixes, _ = env
    assert prefix_bridge.resolve_prefix("ubisoft", "720") == \
        prefixes / "ubisoft" / "720"


def test_missing_steam_root_is_a_noop(env):
    assert prefix_bridge.sync_bridges(None)["linked"] == 0


def test_missing_games_map_is_a_noop(env):
    _, _, steam_root = env
    assert prefix_bridge.sync_bridges(steam_root)["linked"] == 0


# --------------------------------------------------------------------------
# The compat-tool half of the sweep
# --------------------------------------------------------------------------
def test_sweep_prunes_dead_compat_tool_links(env):
    """A Proton removed or upgraded in place must not leave a dangling link.

    Creation happens at launch (``prefix_setup``), where the tool in use is
    known; pruning is the part that needs no launch, so it lives here.
    """
    data, _, steam_root = env
    bridge_root = data / "protontricks-tools"
    bridge_root.mkdir(parents=True)
    dead = bridge_root / f"{compat_tool_bridge.LINK_PREFIX}GE-Proton11-3"
    dead.symlink_to(data / "removed-proton")

    result = prefix_bridge.sync_bridges(steam_root)

    assert result["tools_pruned"] == 1
    assert not dead.exists() and not dead.is_symlink()


def test_sweep_reports_tool_bridge_state_without_raising(env):
    """The tool sweep is optional tooling: it reports, it never fails a sync."""
    _, _, steam_root = env
    result = prefix_bridge.sync_bridges(steam_root)
    assert result["tools_pruned"] == 0
    assert result["tools_flatpak"] == "absent"


def test_tool_sweep_failure_never_breaks_the_prefix_sweep(env, monkeypatch):
    data, prefixes, steam_root = env
    make_prefix(prefixes, "Sugar")
    write_games_map(data, [("epic:Sugar", "/g/rl.exe", "/g", -487067706)])
    monkeypatch.setattr(
        compat_tool_bridge, "prune_dead_links",
        lambda: (_ for _ in ()).throw(OSError("boom")),
    )

    result = prefix_bridge.sync_bridges(steam_root)

    assert result["linked"] == 1          # the prefix bridge still ran
    assert result["tools_flatpak"] == "failed"
